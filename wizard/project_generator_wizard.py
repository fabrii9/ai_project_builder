# -*- coding: utf-8 -*-
"""
ai.project.wizard — Wizard de generación de proyectos con IA.

Flujo:
  1. El usuario ingresa instrucciones en lenguaje natural (state=draft).
  2. Se llama a OpenAI con un prompt estructurado.
  3. OpenAI devuelve JSON con project, stages y tasks.
  4. Se crea el proyecto en Odoo (state=done).
  5. Se muestra resumen y enlace al proyecto creado.
  En caso de error: state=error con mensaje descriptivo.
"""

import json
import logging
import re
import time

import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

OPENAI_BASE_URL = 'https://api.openai.com/v1'
MAX_RETRIES = 3
RETRY_DELAY = 2  # segundos base para backoff

# ---------------------------------------------------------------------------
# Prompt del sistema — instrucciones para OpenAI
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Eres un experto en gestión de proyectos. Tu tarea es analizar las instrucciones o descripción proporcionadas y generar un plan de proyecto estructurado y detallado.

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido y bien formado, sin texto adicional, sin bloques de código markdown (no uses ```json), sin explicaciones. Solo el JSON puro.

El JSON debe tener exactamente esta estructura:
{
  "project": {
    "name": "Nombre del Proyecto",
    "description": "Descripción clara y concisa del proyecto"
  },
  "stages": [
    {"name": "Nombre de la Etapa 1", "sequence": 1, "description": "Descripción de la etapa"},
    {"name": "Nombre de la Etapa 2", "sequence": 2, "description": "Descripción de la etapa"}
  ],
  "tasks": [
    {
      "name": "Nombre de la Tarea",
      "stage": "Nombre exacto de la etapa donde va esta tarea",
      "description": "Descripción detallada de la tarea y sus entregables",
      "sequence": 1,
      "priority": "0"
    }
  ]
}

Reglas obligatorias:
- Crea entre 3 y 7 etapas lógicas y secuenciales para el tipo de proyecto descrito.
- Crea entre 6 y 20 tareas distribuidas coherentemente entre las etapas.
- El campo "stage" en cada tarea DEBE coincidir exactamente (mismo texto) con el "name" de una de las etapas.
- El campo "priority" puede ser "0" (normal) o "1" (alta).
- Responde siempre en el mismo idioma que las instrucciones del usuario.
- Los nombres de etapas y tareas deben ser específicos y accionables, no genéricos.
"""


class AiProjectWizard(models.TransientModel):
    _name = 'ai.project.wizard'
    _description = 'Wizard — Generador de Proyectos con IA'

    # ------------------------------------------------------------------
    # Campos del wizard
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection=[
            ('draft', 'Configurar'),
            ('preview', 'Previsualizar'),
            ('done', 'Completado'),
            ('error', 'Error'),
        ],
        string='Estado',
        default='draft',
        readonly=True,
    )

    # --- Entrada ---
    config_id = fields.Many2one(
        comodel_name='ai.project.config',
        string='Configuración OpenAI',
        required=True,
        help='Selecciona la configuración con la API Key y modelo a utilizar.',
    )
    instructions = fields.Text(
        string='Instrucciones del Proyecto',
        required=True,
        help='Describe el proyecto que deseas crear. Sé lo más detallado posible: '
             'objetivos, alcance, tecnologías, equipo, plazos, etc.',
    )

    # --- Preview (JSON recibido) ---
    preview_json = fields.Text(
        string='Respuesta IA (JSON)',
        readonly=True,
        help='JSON estructurado devuelto por OpenAI.',
    )
    preview_summary = fields.Html(
        string='Resumen del Plan',
        readonly=True,
        help='Vista previa del plan de proyecto antes de crearlo en Odoo.',
    )

    # --- Resultado ---
    project_id = fields.Many2one(
        comodel_name='project.project',
        string='Proyecto Creado',
        readonly=True,
    )
    result_summary = fields.Html(
        string='Resultado',
        readonly=True,
    )
    error_message = fields.Text(
        string='Detalle del Error',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Acciones del wizard
    # ------------------------------------------------------------------

    def action_generate_preview(self):
        """Llama a OpenAI y muestra la previsualización del plan sin crear nada."""
        self.ensure_one()
        raw_json = self._call_openai()
        data = self._parse_and_validate(raw_json)

        # Construir HTML de preview
        preview_html = self._build_preview_html(data)

        self.write({
            'state': 'preview',
            'preview_json': raw_json,
            'preview_summary': preview_html,
        })
        return self._reopen_wizard()

    def action_confirm_create(self):
        """Crea el proyecto, etapas y tareas en Odoo con los datos previsualizados."""
        self.ensure_one()
        if not self.preview_json:
            raise UserError(_('No hay datos de previsualización. Vuelva al paso anterior.'))

        data = self._parse_and_validate(self.preview_json)
        project = self._create_project_records(data)

        tasks_count = len(data.get('tasks', []))
        stages_count = len(data.get('stages', []))

        result_html = (
            f'<p><strong>✅ ¡Proyecto creado exitosamente!</strong></p>'
            f'<ul>'
            f'<li><strong>Proyecto:</strong> {data["project"]["name"]}</li>'
            f'<li><strong>Etapas creadas:</strong> {stages_count}</li>'
            f'<li><strong>Tareas creadas:</strong> {tasks_count}</li>'
            f'</ul>'
            f'<p>Haz clic en <em>Abrir Proyecto</em> para verlo.</p>'
        )

        self.write({
            'state': 'done',
            'project_id': project.id,
            'result_summary': result_html,
        })
        return self._reopen_wizard()

    def action_open_project(self):
        """Abre el proyecto creado en una vista de formulario."""
        self.ensure_one()
        if not self.project_id:
            raise UserError(_('No hay ningún proyecto creado todavía.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'project.project',
            'res_id': self.project_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_reset(self):
        """Vuelve al estado inicial para empezar de nuevo."""
        self.write({
            'state': 'draft',
            'preview_json': False,
            'preview_summary': False,
            'project_id': False,
            'result_summary': False,
            'error_message': False,
        })
        return self._reopen_wizard()

    # ------------------------------------------------------------------
    # Lógica interna: llamada a OpenAI
    # ------------------------------------------------------------------

    def _call_openai(self):
        """
        Envía las instrucciones a OpenAI y devuelve el string JSON crudo.

        :return: str — JSON crudo devuelto por OpenAI
        :raises UserError: si ocurre un error de red, autenticación o API
        """
        config = self.config_id
        base_url = config.endpoint or OPENAI_BASE_URL
        url = f'{base_url}/chat/completions'

        headers = {
            'Authorization': f'Bearer {config.api_key}',
            'Content-Type': 'application/json',
        }
        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': self.instructions},
        ]
        payload = {
            'model': config.model_name,
            'messages': messages,
            'temperature': config.temperature,
            'max_tokens': config.max_tokens,
            # Pedir respuesta en JSON puro
            'response_format': {'type': 'json_object'},
        }

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _logger.info(
                    '[AI Project Builder] Llamando OpenAI — modelo=%s intento=%d',
                    config.model_name, attempt,
                )
                resp = requests.post(url, headers=headers, json=payload, timeout=(15, 90))
                resp.raise_for_status()
                data = resp.json()
                content = data['choices'][0]['message']['content']
                _logger.info(
                    '[AI Project Builder] Respuesta recibida — tokens=%s',
                    data.get('usage', {}).get('total_tokens', '?'),
                )
                return content

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else 0
                body = e.response.text[:300] if e.response else str(e)
                if status == 401:
                    raise UserError(
                        _('API Key inválida o sin permisos (HTTP 401). '
                          'Verifique la configuración OpenAI.')
                    )
                if status == 429:
                    wait = RETRY_DELAY * attempt
                    _logger.warning(
                        '[AI Project Builder] Rate limit OpenAI. Reintentando en %ss', wait
                    )
                    time.sleep(wait)
                    last_error = UserError(
                        _('OpenAI rate limit alcanzado. Intente nuevamente en unos segundos.')
                    )
                    continue
                if status >= 500:
                    wait = RETRY_DELAY * attempt
                    _logger.warning(
                        '[AI Project Builder] Error servidor OpenAI %d. Reintentando...', status
                    )
                    time.sleep(wait)
                    last_error = UserError(_('Error en los servidores de OpenAI (HTTP %s).') % status)
                    continue
                raise UserError(_('Error HTTP %s al llamar a OpenAI: %s') % (status, body))

            except requests.exceptions.ConnectionError:
                raise UserError(
                    _('No se pudo conectar con OpenAI. Verifique la conexión a internet y el endpoint.')
                )
            except requests.exceptions.Timeout:
                last_error = UserError(
                    _('Timeout al esperar respuesta de OpenAI. Intente nuevamente.')
                )
                continue

        raise last_error or UserError(_('No se pudo obtener respuesta de OpenAI tras %d intentos.') % MAX_RETRIES)

    # ------------------------------------------------------------------
    # Lógica interna: parseo y validación del JSON
    # ------------------------------------------------------------------

    def _parse_and_validate(self, raw_json):
        """
        Parsea el JSON recibido de OpenAI y valida su estructura.

        :param raw_json: str — JSON crudo
        :return: dict — datos validados
        :raises UserError: si el JSON es inválido o le faltan campos
        """
        # Limpiar posibles bloques markdown si el modelo los incluyó de todas formas
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw_json.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned.strip())

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            _logger.error('[AI Project Builder] JSON inválido: %s\nContenido: %s', e, raw_json[:500])
            raise UserError(
                _('La respuesta de OpenAI no es un JSON válido. '
                  'Intente nuevamente o ajuste las instrucciones.\nDetalle: %s') % str(e)
            )

        # Validar estructura mínima
        if 'project' not in data or 'name' not in data.get('project', {}):
            raise UserError(
                _('La respuesta no contiene la estructura de proyecto esperada. '
                  'Intente reformular las instrucciones.')
            )
        if not data.get('stages'):
            raise UserError(_('OpenAI no generó etapas para el proyecto.'))
        if not data.get('tasks'):
            raise UserError(_('OpenAI no generó tareas para el proyecto.'))

        # Validar que los stages de las tasks existan
        stage_names = {s['name'] for s in data['stages']}
        invalid_tasks = [
            t['name'] for t in data['tasks']
            if t.get('stage') not in stage_names
        ]
        if invalid_tasks:
            _logger.warning(
                '[AI Project Builder] Tareas con etapa inválida: %s', invalid_tasks
            )
            # Asignar la primera etapa a tareas huérfanas en vez de fallar
            first_stage = data['stages'][0]['name']
            for task in data['tasks']:
                if task.get('stage') not in stage_names:
                    task['stage'] = first_stage

        return data

    # ------------------------------------------------------------------
    # Lógica interna: creación de registros en Odoo
    # ------------------------------------------------------------------

    def _create_project_records(self, data):
        """
        Crea project.project, project.task.type y project.task en Odoo.

        :param data: dict — datos validados con 'project', 'stages', 'tasks'
        :return: project.project record
        """
        ProjectModel = self.env['project.project']
        StageModel = self.env['project.task.type']
        TaskModel = self.env['project.task']

        # 1. Crear proyecto
        project_data = data['project']
        project = ProjectModel.create({
            'name': project_data['name'],
            'description': project_data.get('description', ''),
        })
        _logger.info('[AI Project Builder] Proyecto creado: id=%d name=%s', project.id, project.name)

        # 2. Crear etapas y mapear nombre → record
        stage_map = {}
        for stage_data in data.get('stages', []):
            stage = StageModel.create({
                'name': stage_data['name'],
                'sequence': stage_data.get('sequence', 10),
                'project_ids': [(4, project.id)],
            })
            stage_map[stage_data['name']] = stage
            _logger.debug(
                '[AI Project Builder] Etapa creada: id=%d name=%s', stage.id, stage.name
            )

        # 3. Crear tareas
        for idx, task_data in enumerate(data.get('tasks', []), start=1):
            stage = stage_map.get(task_data.get('stage'))
            if not stage:
                # Fallback a primera etapa si no hay coincidencia
                stage = next(iter(stage_map.values()), None)
            if not stage:
                continue

            TaskModel.create({
                'name': task_data['name'],
                'project_id': project.id,
                'stage_id': stage.id,
                'description': task_data.get('description', ''),
                'sequence': task_data.get('sequence', idx * 10),
                'priority': task_data.get('priority', '0'),
            })

        _logger.info(
            '[AI Project Builder] %d tareas creadas en proyecto id=%d',
            len(data.get('tasks', [])), project.id,
        )
        return project

    # ------------------------------------------------------------------
    # Lógica interna: construcción de HTML de preview
    # ------------------------------------------------------------------

    def _build_preview_html(self, data):
        """Construye un HTML con la previsualización del plan de proyecto."""
        project_info = data.get('project', {})
        stages = data.get('stages', [])
        tasks = data.get('tasks', [])

        # Agrupar tareas por etapa
        tasks_by_stage = {}
        for task in tasks:
            stage_name = task.get('stage', 'Sin etapa')
            tasks_by_stage.setdefault(stage_name, []).append(task)

        html = (
            f'<h4 style="color:#875A7B;">📋 {project_info.get("name", "Proyecto")}</h4>'
            f'<p>{project_info.get("description", "")}</p>'
            f'<p><strong>{len(stages)} etapas</strong> · <strong>{len(tasks)} tareas</strong></p>'
            f'<hr/>'
        )
        for stage in stages:
            stage_name = stage.get('name', '')
            stage_tasks = tasks_by_stage.get(stage_name, [])
            html += f'<p><strong>📌 {stage_name}</strong>'
            if stage.get('description'):
                html += f' — <em>{stage["description"]}</em>'
            html += '</p><ul>'
            for task in stage_tasks:
                priority_badge = ' ⭐' if task.get('priority') == '1' else ''
                html += f'<li>{task.get("name", "")}{priority_badge}'
                if task.get('description'):
                    html += f'<br/><small style="color:#666;">{task["description"][:120]}{"..." if len(task.get("description",""))>120 else ""}</small>'
                html += '</li>'
            if not stage_tasks:
                html += '<li><em>Sin tareas asignadas</em></li>'
            html += '</ul>'

        return html

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reopen_wizard(self):
        """Devuelve la acción para mantener el wizard abierto con el estado actualizado."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }
