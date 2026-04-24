# -*- coding: utf-8 -*-
"""
ai.project.config — Configuración de conexión al proveedor LLM para el generador de proyectos.

Proveedores soportados:
- OpenAI (GPT-4o, GPT-4o-mini, etc.)
- Google Gemini (gemini-2.0-flash, gemini-1.5-pro, etc.)
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

GEMINI_MODELS = [
    ('gemini-2.0-flash', 'Gemini 2.0 Flash'),
    ('gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite'),
    ('gemini-1.5-pro', 'Gemini 1.5 Pro'),
    ('gemini-1.5-flash', 'Gemini 1.5 Flash'),
]

OPENAI_BASE_URL = 'https://api.openai.com/v1'
GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'


class AiProjectConfig(models.Model):
    _name = 'ai.project.config'
    _description = 'Configuración LLM — Generador de Proyectos'
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
    # Proveedor
    # ------------------------------------------------------------------
    provider = fields.Selection(
        selection=[
            ('openai', 'OpenAI'),
            ('gemini', 'Google Gemini'),
        ],
        string='Proveedor',
        required=True,
        default='openai',
    )

    # ------------------------------------------------------------------
    # Conexión OpenAI
    # ------------------------------------------------------------------
    api_key = fields.Char(
        string='API Key',
        required=True,
        help='API Key del proveedor seleccionado.',
    )
    openai_model = fields.Selection(
        selection=OPENAI_MODELS,
        string='Modelo OpenAI',
        default='gpt-4o-mini',
    )
    openai_endpoint = fields.Char(
        string='Endpoint OpenAI (opcional)',
        help='URL base personalizada. Dejar vacío para usar https://api.openai.com/v1',
    )

    # ------------------------------------------------------------------
    # Conexión Gemini
    # ------------------------------------------------------------------
    gemini_model = fields.Selection(
        selection=GEMINI_MODELS,
        string='Modelo Gemini',
        default='gemini-2.0-flash',
    )

    # ------------------------------------------------------------------
    # Parámetros comunes
    # ------------------------------------------------------------------
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

    # Campo calculado: nombre del modelo activo según proveedor
    model_name = fields.Char(
        string='Modelo activo',
        compute='_compute_model_name',
        store=False,
    )

    # ------------------------------------------------------------------
    # Notas
    # ------------------------------------------------------------------
    notes = fields.Text(string='Notas internas')

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends('provider', 'openai_model', 'gemini_model')
    def _compute_model_name(self):
        for rec in self:
            if rec.provider == 'gemini':
                rec.model_name = rec.gemini_model or ''
            else:
                rec.model_name = rec.openai_model or ''

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
    # Acción: probar conexión
    # ------------------------------------------------------------------
    def action_test_connection(self):
        """Prueba la conexión al proveedor con un mensaje mínimo."""
        self.ensure_one()
        if self.provider == 'gemini':
            self._test_gemini()
        else:
            self._test_openai()

    def _test_openai(self):
        base_url = self.openai_endpoint or OPENAI_BASE_URL
        url = f'{base_url}/chat/completions'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.openai_model,
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': 5,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(10, 30))
            resp.raise_for_status()
            raise UserError(
                _('✅ Conexión exitosa con OpenAI (modelo: %s).') % self.openai_model
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

    def _test_gemini(self):
        model = self.gemini_model
        url = f'{GEMINI_BASE_URL}/models/{model}:generateContent?key={self.api_key}'
        payload = {
            'contents': [{'parts': [{'text': 'ping'}]}],
            'generationConfig': {'maxOutputTokens': 5},
        }
        try:
            resp = requests.post(url, json=payload, timeout=(10, 30))
            resp.raise_for_status()
            raise UserError(
                _('✅ Conexión exitosa con Google Gemini (modelo: %s).') % model
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            body = e.response.text if e.response else str(e)
            if status == 400:
                raise UserError(_('❌ API Key inválida o modelo no disponible (HTTP 400): %s') % body[:200])
            if status == 403:
                raise UserError(_('❌ Sin permisos para usar Gemini (HTTP 403).'))
            raise UserError(_('❌ Error HTTP %s: %s') % (status, body[:200]))
        except requests.exceptions.ConnectionError:
            raise UserError(_('❌ No se pudo conectar con Google Gemini. Verificar red.'))
        except requests.exceptions.Timeout:
            raise UserError(_('❌ Timeout al intentar conectar con Google Gemini.'))
