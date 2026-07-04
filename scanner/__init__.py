"""
scanner — Market Scanner.

Responsável por percorrer automaticamente categorias e subcategorias,
descobrir novos produtos/vendedores/anúncios, e registrar fila de coleta.

Nenhum usuário inicia esse processo — o scanner roda continuamente.
"""

from scanner.scanner import MarketScanner, ScanResult

__all__ = ["MarketScanner", "ScanResult"]
