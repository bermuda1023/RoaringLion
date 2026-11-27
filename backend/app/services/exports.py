"""Phase 6/9: Excel-ready CSV and printable PDF Tear-Sheet exports with full Breadcrumb Manifest provenance."""

from __future__ import annotations

import csv
import html
import io

from app.models.domain import DagEvalResponse, PortfolioRollupResponse, ProgrammeDeal


def build_portfolio_csv(rollup: PortfolioRollupResponse) -> str:
    """Serialize a PortfolioRollupResponse to an Excel-compatible CSV with manifest provenance."""
    buf = io.StringIO()
    w = csv.writer(buf)

    w.writerow(["RoaringLion Portfolio Rollup Export"])
    w.writerow(["Portfolio", rollup.portfolio_name])
    w.writerow(["View", rollup.view.value])
    w.writerow(["Currency", rollup.currency])
    w.writerow(["Trials", rollup.num_trials])
    w.writerow(["Manifest ID", rollup.manifest.manifest_id])
    w.writerow(["Output Hash", rollup.manifest.output_hash])
    w.writerow(["Engine Version", rollup.manifest.engine_version])
    w.writerow([])

    w.writerow(["Metric", "Value"])
    w.writerow(["Total Capital ($M)", rollup.total_capital])
    w.writerow(["Deployed Collateral ($M)", rollup.deployed_collateral])
    w.writerow(["Include Hedges", rollup.include_hedges])
    w.writerow(["Gross Premium ($M)", rollup.gross_premium])
    w.writerow(["Hedge Premium ($M)", rollup.hedge_premium])
    w.writerow(["Net Premium ($M)", rollup.net_premium])
    w.writerow(["Expected RIP ($M)", rollup.expected_rip])
    w.writerow(["Expected Loss ($M)", rollup.expected_loss])
    w.writerow(["Expected Hedge Recovery ($M)", rollup.expected_hedge_recovery])
    w.writerow(["Expected Profit ($M)", rollup.expected_profit])
    w.writerow(["Mean ROC (%)", rollup.mean_roc_pct])
    w.writerow(["No-Event ROC (%)", rollup.no_event_roc_pct])
    w.writerow(["Break-Even Prob (%)", rollup.break_even_prob_pct])
    w.writerow(["Return on 1-in-250 TVaR (%)", rollup.return_on_tvar_pct])
    w.writerow(["1-in-250 VaR ($M)", rollup.portfolio_var_250])
    w.writerow(["1-in-250 TVaR ($M)", rollup.portfolio_tvar_250])
    w.writerow(["Euler Co-TVaR Conservation Diff", rollup.cotvar_sum_check_diff])
    w.writerow([])

    w.writerow(["Risk Appetite Guidelines"])
    w.writerow(["Percentile", "Threshold ROC (%)", "Actual ROC (%)", "Status"])
    for g in rollup.guidelines:
        w.writerow([g.label, g.threshold_roc_pct, g.actual_roc_pct, "PASS" if g.passed else "BREACH"])
    w.writerow([])

    w.writerow(["Euler Co-TVaR Layer Allocation"])
    w.writerow([
        "Programme ID",
        "Cedent",
        "Layer",
        "Direction",
        "Limit ($M)",
        "Premium ($M)",
        "Signed Line",
        "EL ($M)",
        "Expected RIP ($M)",
        "Standalone TVaR 250 ($M)",
        "Euler Co-TVaR 250 ($M)",
        "Div Credit (%)",
        "Marginal ROC (%)",
    ])
    for r in rollup.layer_allocations:
        w.writerow([
            r.programme_id,
            r.cedent_name,
            r.layer_name,
            "Hedge" if r.is_hedge else "Long",
            r.limit,
            r.premium,
            r.signed_line,
            r.el,
            r.expected_rip,
            r.standalone_tvar_250,
            r.euler_cotvar_250,
            r.diversification_credit_pct,
            r.marginal_roc_pct,
        ])
    w.writerow([])
    w.writerow(["Breadcrumb Trail"])
    w.writerow(["Entity Type", "Entity ID", "Label", "Version", "Content Hash"])
    for b in rollup.manifest.breadcrumb:
        w.writerow([b.entity_type, b.entity_id, b.label, b.version or "", b.content_hash or ""])

    return buf.getvalue()


def build_portfolio_tearsheet_html(rollup: PortfolioRollupResponse) -> str:
    """Render a self-contained, print-to-PDF institutional portfolio tear-sheet."""
    esc = html.escape
    guideline_rows = "".join(
        f"<tr><td>{esc(g.label)}</td><td class='num'>{g.threshold_roc_pct:.1f}%</td>"
        f"<td class='num'>{g.actual_roc_pct:.2f}%</td>"
        f"<td class='num {'pass' if g.passed else 'fail'}'>{'PASS' if g.passed else 'BREACH'}</td></tr>"
        for g in rollup.guidelines
    )
    alloc_rows = "".join(
        f"<tr><td>{esc(r.cedent_name)}</td><td>{esc(r.layer_name)}</td>"
        f"<td>{'Hedge' if r.is_hedge else 'Long'}</td>"
        f"<td class='num'>${r.limit:.1f}m</td><td class='num'>${r.premium:.2f}m</td>"
        f"<td class='num'>${r.el:.2f}m</td><td class='num'>${r.standalone_tvar_250:.2f}m</td>"
        f"<td class='num'><b>${r.euler_cotvar_250:.2f}m</b></td>"
        f"<td class='num'>{r.diversification_credit_pct:.1f}%</td>"
        f"<td class='num'>{r.marginal_roc_pct:.1f}%</td></tr>"
        for r in rollup.layer_allocations
    )
    crumbs = " &rsaquo; ".join(
        f"<span><b>{esc(b.entity_type)}</b>: {esc(b.label)}{' ' + esc(b.version) if b.version else ''}</span>"
        for b in rollup.manifest.breadcrumb
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>RoaringLion Portfolio Tear-Sheet — {esc(rollup.portfolio_name)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #0f172a; margin: 28px; font-size: 12px; }}
  .hdr {{ display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #0f172a; padding-bottom: 8px; margin-bottom: 16px; }}
  .hdr h1 {{ margin: 0; font-size: 18px; letter-spacing: 0.02em; }}
  .meta {{ color: #475569; font-size: 11px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin-bottom: 16px; }}
  .kpi {{ border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px 10px; background: #f8fafc; }}
  .kpi .lbl {{ font-size: 10px; text-transform: uppercase; color: #64748b; }}
  .kpi .val {{ font-size: 16px; font-weight: 700; margin-top: 2px; }}
  h2 {{ font-size: 13px; margin: 16px 0 6px; text-transform: uppercase; color: #334155; letter-spacing: 0.04em; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
  th, td {{ border-bottom: 1px solid #e2e8f0; padding: 5px 7px; text-align: left; }}
  th {{ background: #f1f5f9; font-size: 10px; text-transform: uppercase; color: #475569; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .pass {{ color: #15803d; font-weight: 700; }}
  .fail {{ color: #b91c1c; font-weight: 700; }}
  .crumb {{ margin-top: 14px; padding: 8px 10px; background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 5px; font-size: 10px; color: #334155; }}
  .no-print {{ margin-bottom: 12px; }}
  @media print {{ .no-print {{ display: none; }} body {{ margin: 12px; }} }}
</style></head><body>
<div class="no-print"><button onclick="window.print()">Print / Save as PDF</button></div>
<div class="hdr">
  <div><h1>ROARING LION — Portfolio Tear-Sheet</h1><div class="meta">{esc(rollup.portfolio_name)} · View: <b>{esc(rollup.view.value)}</b> · Currency: {esc(rollup.currency)} · {rollup.num_trials:,} trials</div></div>
  <div class="meta">Manifest: <code>{esc(rollup.manifest.manifest_id)}</code> · Hash: <code>#{esc(rollup.manifest.output_hash[:12])}</code></div>
</div>
<div class="kpis">
  <div class="kpi"><div class="lbl">Mean ROC</div><div class="val">{rollup.mean_roc_pct:.2f}%</div></div>
  <div class="kpi"><div class="lbl">Return on 1-in-250 TVaR</div><div class="val">{rollup.return_on_tvar_pct:.2f}%</div></div>
  <div class="kpi"><div class="lbl">Net / Hedge Prem</div><div class="val">${rollup.net_premium:.2f}m / ${rollup.hedge_premium:.2f}m</div></div>
  <div class="kpi"><div class="lbl">Expected Profit</div><div class="val">${rollup.expected_profit:.2f}m</div></div>
  <div class="kpi"><div class="lbl">1-in-250 TVaR</div><div class="val">${rollup.portfolio_tvar_250:.2f}m</div></div>
  <div class="kpi"><div class="lbl">Total Capital</div><div class="val">${rollup.total_capital:.0f}m</div></div>
</div>
<h2>Risk Appetite Guidelines</h2>
<table><thead><tr><th>Percentile</th><th class="num">Threshold</th><th class="num">Actual</th><th class="num">Status</th></tr></thead><tbody>{guideline_rows}</tbody></table>
<h2>Euler Co-TVaR Marginal Capital Allocation (1-in-250 / 99.6th%)</h2>
<table><thead><tr><th>Cedent</th><th>Layer</th><th>Dir</th><th class="num">Limit</th><th class="num">Premium</th><th class="num">EL</th><th class="num">Standalone TVaR</th><th class="num">Euler Co-TVaR</th><th class="num">Div Credit</th><th class="num">Marginal ROC</th></tr></thead><tbody>{alloc_rows}</tbody></table>
<div class="crumb"><b>Breadcrumb Provenance:</b> {crumbs}</div>
</body></html>"""


def build_programme_tearsheet_html(programme: ProgrammeDeal, dag_eval: DagEvalResponse) -> str:
    """Render an underwriting committee deal tear-sheet comparing Unadjusted vs Adjusted DAG stats."""
    esc = html.escape
    unadj = {m.node_id: m for m in dag_eval.views.get("UNADJUSTED", [])}
    adj = dag_eval.views.get("ADJUSTED", [])
    rows = []
    for a in adj:
        u = unadj.get(a.node_id)
        u_el = u.el if u else 0.0
        delta = ((a.el - u_el) / u_el * 100.0) if u_el > 1e-6 else 0.0
        tvar250 = next((pt.tvar for pt in a.ep_points if abs(pt.return_period - 250.0) < 1e-6), 0.0)
        elr_str = f"{a.elr * 100.0:.1f}%" if a.elr is not None else "—"
        rows.append(
            f"<tr><td>{esc(a.node_label)}</td><td>{esc(a.node_type.value)}</td>"
            f"<td class='num'>${u_el:.3f}m</td><td class='num'><b>${a.el:.3f}m</b></td>"
            f"<td class='num'>{delta:+.1f}%</td><td class='num'>${a.sd:.3f}m</td>"
            f"<td class='num'>${a.expected_rip:.3f}m</td><td class='num'>${tvar250:.2f}m</td>"
            f"<td class='num'>{elr_str}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>RoaringLion Deal Paper — {esc(programme.cedent_name)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #0f172a; margin: 28px; font-size: 12px; }}
  .hdr {{ display: flex; justify-content: space-between; border-bottom: 2px solid #0f172a; padding-bottom: 8px; margin-bottom: 14px; }}
  h1 {{ margin: 0; font-size: 18px; }}
  .meta {{ color: #475569; font-size: 11px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
  th, td {{ border-bottom: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; }}
  th {{ background: #f1f5f9; font-size: 10px; text-transform: uppercase; color: #475569; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  @media print {{ .no-print {{ display: none; }} body {{ margin: 12px; }} }}
</style></head><body>
<div class="no-print"><button onclick="window.print()">Print / Save as PDF</button></div>
<div class="hdr">
  <div><h1>ROARING LION — Underwriting Deal Tear-Sheet</h1>
  <div class="meta">{esc(programme.cedent_name)} — {esc(programme.programme_name)} ({esc(programme.renewal_period)}) · Direction: <b>{'Hedge (Ceded)' if programme.is_hedge else 'Long (Assumed)'}</b> · Status: <b>{esc(programme.status.value)}</b> · UW: {esc(programme.underwriter)} · Broker: {esc(programme.broker)}</div></div>
  <div class="meta">Graph v{dag_eval.graph_version} · Manifest: <code>{esc(dag_eval.manifest.manifest_id)}</code></div>
</div>
<table><thead><tr><th>Node</th><th>Type</th><th class="num">Unadj EL</th><th class="num">Adj EL</th><th class="num">Uplift Δ</th><th class="num">Adj SD</th><th class="num">Exp RIP</th><th class="num">1-in-250 TVaR</th><th class="num">Adj ELR</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
</body></html>"""
