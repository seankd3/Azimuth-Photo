# Pages Feature

Owns: HTML page routes and template selection for the main screens.
Depends on: injected Jinja templates, injected template context, and templates in `web/templates/`.
Public routes: `/`, `/library`, `/rankings`, `/compare`, `/people`, `/settings`, `/catalog`.
Frontend modules: `static/js/bootstrap.js` and `static/js/legacy/app.js` load the current no-build frontend; page behavior fans out to feature folders under `static/js/`.
Data repositories: None directly; page routes should stay thin.
Tests to run: `cd web && .venv/bin/python -m unittest test_modular_contracts -q`.
Do not touch: public page URLs, template names, or template-visible context keys during route extraction.
