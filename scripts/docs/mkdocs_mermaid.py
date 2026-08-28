"""MkDocs extension hook that keeps Mermaid blocks outside Material code fences."""

from html import escape


def format_mermaid(source: str, language: str, css_class: str, options: dict, md, **kwargs) -> str:
    """Emit a Mermaid container that is rendered by docs/javascripts/mermaid.js."""
    return f'<div class="mermaid-diagram" data-mermaid-source="{escape(source, quote=True)}">{escape(source)}</div>'
