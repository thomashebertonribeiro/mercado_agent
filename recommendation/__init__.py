"""
recommendation — Recommendation Engine.

Gera rankings automáticos como:
  - Top oportunidades do dia
  - Top categorias
  - Produtos em crescimento
  - Produtos abandonados
  - Categorias promissoras
  - Produtos com baixa concorrência
  - Categorias mais aquecidas
  - Novos produtos promissores
  - Produtos que perderam concorrentes
  - Produtos cujo preço médio aumentou
"""

from recommendation.engine import RecommendationEngine

__all__ = ["RecommendationEngine"]
