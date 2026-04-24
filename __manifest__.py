# -*- coding: utf-8 -*-
# Part of ISP360
# License: LGPL-3

{
    'name': 'AI Project Builder',
    'version': '19.0.1.0.0',
    'category': 'Project',
    'summary': 'Genera proyectos, etapas y tareas automáticamente con OpenAI',
    'description': """
        AI Project Builder
        ======================
        Permite generar proyectos completos en Odoo a partir de texto libre usando
        la API de OpenAI (GPT-4o, GPT-4o-mini, etc.).

        Funcionalidades:
        - Configuración de credenciales OpenAI (API Key, modelo, temperatura)
        - Wizard de generación: el usuario describe el proyecto en lenguaje natural
        - Llamada a OpenAI con prompt estructurado para obtener JSON del proyecto
        - Creación automática de project.project, etapas (project.task.type) y tareas
        - Vista previa del resultado antes de confirmar
        - Acceso directo al proyecto generado
    """,
    'author': 'ISP360',
    'website': 'https://isp360.com.ar',
    'depends': [
        'base',
        'project',
        'mail',
    ],
    'data': [
        # Security
        'security/ir.model.access.csv',
        # Views
        'views/ai_project_config_views.xml',
        'wizard/project_generator_wizard_view.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
