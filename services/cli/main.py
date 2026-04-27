"""
CloudSense CLI
===============
Usage:
  cloudsense scan --provider aws --start 2024-01-01 --end 2024-02-01
  cloudsense costs overview
  cloudsense costs by-service --provider aws --days 30
  cloudsense connectors list
  cloudsense connectors add aws --account-id 123456789012

Install:
  pip install cloudsense          # from PyPI
  poetry install                  # from source
  cloudsense --help
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

app     = typer.Typer(
    name="cloudsense",
    help="🔍 CloudSense — FinOps CLI for AWS, Azure & GCP cost optimisation",
    rich_markup_mode="rich",
)
console = Console()

# Sub-command groups
costs_app      = typer.Typer(help="Query billing cost data from ClickHouse")
connectors_app = typer.Typer(help="Manage cloud connector configurations")

app.add_typer(costs_app,      name="costs")
app.add_typer(connectors_app, name="connectors")


# ── Version ────────────────────────────────────────────────────────────────────

def version_callback(value: bool) -> None:
    if value:
        typer.echo("CloudSense CLI v0.1.0")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", callback=version_callback, is_eager=True),
) -> None:
    """CloudSense — FinOps multi-agent platform CLI"""


# ── scan ──────────────────────────────────────────────────────────────────────

@app.command()
def scan(
    provider: str  = typer.Option(..., "--provider", "-p",
                                   help="Cloud provider: aws | azure | gcp"),
    connector_id: str = typer.Option(..., "--connector-id", "-c",
                                      help="Connector / account ID"),
    start: str = typer.Option(
        str(date.today() - timedelta(days=30)),
        "--start", "-s", help="Start date YYYY-MM-DD"
    ),
    end: str   = typer.Option(str(date.today()), "--end", "-e", help="End date YYYY-MM-DD"),
    api_url: str = typer.Option(
        os.environ.get("CLOUDSENSE_API_URL", "http://localhost:8000"),
        "--api-url", help="CloudSense API base URL"
    ),
) -> None:
    """Trigger a billing data ingestion for a cloud connector."""
    import httpx

    console.print(f"[bold]CloudSense[/bold] · triggering ingestion")
    console.print(f"  Provider:  [cyan]{provider}[/cyan]")
    console.print(f"  Connector: [cyan]{connector_id}[/cyan]")
    console.print(f"  Period:    [cyan]{start} → {end}[/cyan]")

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=console) as progress:
        task = progress.add_task("Sending ingestion request...", total=None)

        try:
            resp = httpx.post(
                f"{api_url}/api/v1/ingestion/trigger",
                json={
                    "provider":      provider.lower(),
                    "connector_id":  connector_id,
                    "start_date":    start,
                    "end_date":      end,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            progress.stop()
            console.print(f"\n[green]✓ Ingestion queued[/green]")
            console.print(f"  Task ID: [dim]{data['task_id']}[/dim]")
            console.print(f"  Status:  {data['status']}")
        except Exception as exc:
            progress.stop()
            console.print(f"\n[red]✗ Error: {exc}[/red]")
            raise typer.Exit(1)


# ── costs overview ────────────────────────────────────────────────────────────

@costs_app.command("overview")
def costs_overview(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to look back"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p"),
    api_url: str = typer.Option(
        os.environ.get("CLOUDSENSE_API_URL", "http://localhost:8000"), "--api-url"
    ),
) -> None:
    """Show multi-cloud spend overview."""
    import httpx
    from datetime import date, timedelta

    end   = date.today()
    start = end - timedelta(days=days)

    params = {"start_date": str(start), "end_date": str(end)}
    if provider:
        params["provider"] = provider.lower()

    try:
        resp = httpx.get(f"{api_url}/api/v1/costs/overview", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        console.print(f"[red]✗ API error: {exc}[/red]")
        raise typer.Exit(1)

    items = data["data"]
    meta  = data["meta"]

    table = Table(title=f"☁ Cloud Spend Overview  [{start} → {end}]",
                  show_header=True, header_style="bold")
    table.add_column("Provider",       style="cyan",   no_wrap=True)
    table.add_column("Account",        style="dim",    no_wrap=True)
    table.add_column("Effective Cost", justify="right", style="green")
    table.add_column("List Cost",      justify="right", style="dim")
    table.add_column("Savings",        justify="right", style="yellow")
    table.add_column("Savings %",      justify="right", style="yellow")

    total_eff = 0.0
    total_sav = 0.0
    for item in items:
        eff = item["total_effective_cost"]
        sav = item["savings_amount"]
        total_eff += eff
        total_sav += sav
        table.add_row(
            item["provider_name"].upper(),
            item["billing_account_name"] or item["billing_account_id"],
            f"${eff:,.2f}",
            f"${item['total_list_cost']:,.2f}",
            f"${sav:,.2f}",
            f"{item['savings_pct']:.1f}%",
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]", "", f"[bold]${total_eff:,.2f}[/bold]",
        "", f"[bold]${total_sav:,.2f}[/bold]", ""
    )

    console.print()
    console.print(table)
    console.print(f"\n[dim]Query time: {meta['query_ms']:.1f}ms · {len(items)} accounts[/dim]")


# ── costs by-service ─────────────────────────────────────────────────────────

@costs_app.command("by-service")
def costs_by_service(
    days:     int           = typer.Option(30, "--days", "-d"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p"),
    top:      int           = typer.Option(10, "--top", help="Show top N services"),
    api_url:  str           = typer.Option(
        os.environ.get("CLOUDSENSE_API_URL", "http://localhost:8000"), "--api-url"
    ),
) -> None:
    """Show top services by cost."""
    import httpx
    from datetime import date, timedelta

    end   = date.today()
    start = end - timedelta(days=days)

    params: dict = {"start_date": str(start), "end_date": str(end), "top_n": top}
    if provider:
        params["provider"] = provider.lower()

    try:
        resp = httpx.get(f"{api_url}/api/v1/costs/top-services", params=params, timeout=30)
        resp.raise_for_status()
        items = resp.json()["data"]
    except Exception as exc:
        console.print(f"[red]✗ API error: {exc}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"🔥 Top {top} Services by Cost  [{start} → {end}]",
                  show_header=True, header_style="bold")
    table.add_column("#",             justify="right", style="dim")
    table.add_column("Provider",      style="cyan")
    table.add_column("Service",       style="white")
    table.add_column("Category",      style="dim")
    table.add_column("Effective Cost",justify="right", style="green")
    table.add_column("Waste %",       justify="right", style="red")

    for i, item in enumerate(items, 1):
        waste_style = "red" if item["waste_pct"] > 20 else "yellow" if item["waste_pct"] > 5 else "dim"
        table.add_row(
            str(i),
            item["provider_name"].upper(),
            item["service_name"],
            item["service_category"],
            f"${item['total_cost']:,.2f}",
            f"[{waste_style}]{item['waste_pct']:.1f}%[/{waste_style}]",
        )

    console.print()
    console.print(table)


# ── connectors list ──────────────────────────────────────────────────────────

@connectors_app.command("list")
def connectors_list(
    api_url: str = typer.Option(
        os.environ.get("CLOUDSENSE_API_URL", "http://localhost:8000"), "--api-url"
    ),
) -> None:
    """List all configured cloud connectors."""
    import httpx

    try:
        resp = httpx.get(f"{api_url}/api/v1/connectors/", timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        console.print(f"[red]✗ API error: {exc}[/red]")
        raise typer.Exit(1)

    items = data.get("data", [])
    if not items:
        console.print(
            "[yellow]No connectors configured.[/yellow]\n"
            "Run [bold]cloudsense connectors add --help[/bold] to get started."
        )
        return

    table = Table(title="🔌 Configured Connectors", show_header=True, header_style="bold")
    table.add_column("ID",       style="dim",  max_width=10)
    table.add_column("Provider", style="cyan")
    table.add_column("Name",     style="white")
    table.add_column("Account",  style="dim")
    table.add_column("Status",   style="green")
    table.add_column("Last Ingested")

    for c in items:
        status_style = "green" if c["status"] == "active" else "red"
        table.add_row(
            c["id"][:8],
            c["provider"].upper(),
            c["name"],
            c["billing_account_id"],
            f"[{status_style}]{c['status']}[/{status_style}]",
            c.get("last_ingested_at", "Never") or "Never",
        )
    console.print(table)


if __name__ == "__main__":
    app()
