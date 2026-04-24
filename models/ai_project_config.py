# -*- coding: utf-8 -*-
"""
ai.project.config — Configuración de conexión a OpenAI para el generador de proyectos.

Almacena:
- API Key de OpenAI
- Modelo a utilizar (GPT-4o, GPT-4o-mini, etc.)
- Temperatura y max_tokens
"""

import logging
import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

OPENAI_MODELS = [
    ('gpt-4o', 'GPT-4o'),
    ('gpt-4o-mini', 'GPT-4o Mini'),
    ('gpt-4-turbo', 'GPT-4 Turbo'),
    ('gpt-3.5-turbo', 'GPT-3.5 Turbo'),
]

OPENAI_BASE_URL = 'https://api.openai.com/v1'


class AiProjectConfig(models.Model):
    _name = 'ai.project.config'
    _description = 'Configuración OpenAI — Generador de Proyectos'
    _order = 'sequence, name'

    # ------------------------------------------------------------------
    # Campos base
    # ------------------------------------------------------------------
    name = fields.Char(
        string='Nombre de la Configuración',
        required=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Conexión OpenAI
    # ------------------------------------------------------------------
    api_key = fields.Char(
        string='API Key de OpenAI',
        required=True,
        help='Clave de API de OpenAI. Se almacena encriptada.',
    )
    model_name = fields.Selection(
        selection=OPENAI_MODELS,
        string='Modelo',
        required=True,
        default='gpt-4o-mini',
        help='Modelo de OpenAI a utilizar para generar el plan del proyecto.',
    )
    temperature = fields.Float(
        string='Temperatura',
        default=0.3,
        help='Controla la creatividad (0.0 = determinista, 1.0 = muy creativo). '
             'Recomendado: 0.2–0.4 para tareas estructuradas.',
    )
    max_tokens = fields.Integer(
        string='Máx. Tokens',
        default=2048,
        help='Límite de tokens en la respuesta del modelo.',
    )
    endpoint = fields.Char(
        string='Endpoint (opcional)',
        help='URL base personalizada. Dejar vacío para usar https://api.openai.com/v1',
    )

    # ------------------------------------------------------------------
    # Notas
    # ------------------------------------------------------------------
    notes = fields.Text(string='Notas internas')

    # ------------------------------------------------------------------
    # Constrains
    # ------------------------------------------------------------------
    @api.constrains('temperature')
    def _check_temperature(self):
        for rec in self:
            if not (0.0 <= rec.temperature <= 2.0):
                raise ValidationError(_('La temperatura debe estar entre 0.0 y 2.0.'))

    @api.constrains('max_tokens')
    def _check_max_tokens(self):
        for rec in self:
            if rec.max_tokens < 256:
                raise ValidationError(_('El mínimo de tokens permitido es 256.'))

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_test_connection(self):
        """Prueba la conexión a OpenAI con un mensaje mínimo."""
        self.ensure_one()
        base_url = self.endpoint or OPENAI_BASE_URL
        url = f'{base_url}/chat/completions'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.model_name,
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': 5,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(10, 30))
            resp.raise_for_status()
            raise UserError(
                _('✅ Conexión exitosa con OpenAI (modelo: %s).') % self.model_name
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            body = e.response.text if e.response else str(e)
            if status == 401:
                raise UserError(_('❌ API Key inválida o sin permisos (HTTP 401).'))
            raise UserError(_('❌ Error HTTP %s: %s') % (status, body[:200]))
        except requests.exceptions.ConnectionError:
            raise UserError(_('❌ No se pudo conectar con OpenAI. Verificar red/endpoint.'))
        except requests.exceptions.Timeout:
            raise UserError(_('❌ Timeout al intentar conectar con OpenAI.'))
