"""
ai_analyst — Analisador determinístico de dados do Intelligence Engine.

A IA nunca pesquisa.
A IA apenas interpreta dados produzidos pelo Intelligence Engine
e pelo Opportunity Score para gerar relatórios executivos.

Toda conclusão é justificada exclusivamente com os dados recebidos.
Nenhuma informação externa é consultada ou inventada.
"""

from ai_analyst.analyst import AIAnalyst, analyze_product

__all__ = ["AIAnalyst", "analyze_product"]
