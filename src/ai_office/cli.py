"""Command-line interface for AI Office."""

import typer

app = typer.Typer(
    name="ai-office",
    help="人間が定義したワークフローを扱う AI 業務基盤。",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """AI Office のコマンド群。"""
