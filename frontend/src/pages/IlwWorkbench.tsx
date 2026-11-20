import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, type IlwPriceRequest } from '../api/client';
import { BreadcrumbBar } from '../components/BreadcrumbBar';

export function IlwWorkbench() {
  const qc = useQueryClient();
  const [req, setReq] = useState<IlwPriceRequest>({
    name: 'US FL Wind $40B Binary Retro ILW',
    industrySimSetId: 'ind-synth-cat-v1-100k',
    regionPerils: ['US-FL-WS'],
    triggerType: 'BINARY',
    triggerBn: 40,
    exhaustionBn: 60,
    limitM: 25,
    rol: 0.2,
    direction: 'EXTERNAL_RETRO',
    currency: 'USD',
    portfolioView: 'ADJUSTED',
  });

  const priceMut = useMutation({ mutationFn: () => api.priceIlw(req) });
  const promoteMut = useMutation({
    mutationFn: () => api.promoteIlw(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['contracts'] }),
  });

  useEffect(() => {
    priceMut.mutate();
  }, []);

  const d = priceMut.data;

  return (
    <div className="card">
      <div className="card-title">
        <span>Instant ILW Pricing & Portfolio Hedge-Effectiveness Workbench (Background Industry YELT)</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" onClick={() => priceMut.mutate()}>
            Price ILW & Hedge Impact
          </button>
          <button className="btn btn-ghost" onClick={() => promoteMut.mutate()}>
            {promoteMut.isSuccess ? 'Added to CMS' : '+ Promote ILW to CMS'}
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14, padding: 10, background: '#0d1422', borderRadius: 6, border: '1px solid #26324a' }}>
        <input value={req.name} onChange={(e) => setReq({ ...req, name: e.target.value })} style={{ width: 220 }} />
        <select
          value={req.regionPerils[0]}
          onChange={(e) => setReq({ ...req, regionPerils: [e.target.value] })}
        >
          <option value="US-FL-WS">US-FL-WS (Florida Wind)</option>
          <option value="US-ALL-WS">US-ALL-WS (All US Wind)</option>
          <option value="US-TX-WS">US-TX-WS (Texas Wind)</option>
          <option value="US-CA-EQ">US-CA-EQ (California EQ)</option>
          <option value="JP-KANTO-EQ">JP-KANTO-EQ (Japan EQ)</option>
          <option value="EU-DE-WS">EU-DE-WS (Europe Wind)</option>
        </select>
        <select
          value={req.triggerType}
          onChange={(e) => setReq({ ...req, triggerType: e.target.value as IlwPriceRequest['triggerType'] })}
        >
          <option value="BINARY">BINARY (Dead-Cat)</option>
          <option value="PRO_RATA">PRO_RATA (Linear)</option>
          <option value="AGGREGATE">AGGREGATE ILW</option>
          <option value="SECOND_EVENT">SECOND_EVENT ILW</option>
        </select>
        <select
          value={req.direction}
          onChange={(e) => setReq({ ...req, direction: e.target.value as IlwPriceRequest['direction'] })}
        >
          <option value="EXTERNAL_RETRO">CEDED / RETRO HEDGE</option>
          <option value="ASSUMED">ASSUMED (Write ILW)</option>
        </select>
        <label style={{ fontSize: 11, color: '#94a3b8' }}>
          Trigger ($B)
          <input type="number" value={req.triggerBn} onChange={(e) => setReq({ ...req, triggerBn: Number(e.target.value) })} style={{ width: 65, marginLeft: 4 }} />
        </label>
        <label style={{ fontSize: 11, color: '#94a3b8' }}>
          Limit ($M)
          <input type="number" value={req.limitM} onChange={(e) => setReq({ ...req, limitM: Number(e.target.value) })} style={{ width: 65, marginLeft: 4 }} />
        </label>
        <label style={{ fontSize: 11, color: '#94a3b8' }}>
          ROL
          <input type="number" step="0.01" value={req.rol} onChange={(e) => setReq({ ...req, rol: Number(e.target.value) })} style={{ width: 65, marginLeft: 4 }} />
        </label>
      </div>

      {d && (
        <>
          <div className="grid-kpi">
            <div className="kpi">
              <div className="kpi-label">Attach / Exhaust Prob</div>
              <div className="kpi-val">{d.attachProbPct.toFixed(2)}%</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Standalone EL / ELR</div>
              <div className="kpi-val">
                ${d.expectedLossM.toFixed(2)}m ({d.elrPct.toFixed(1)}%)
              </div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Indemnity–Index Corr</div>
              <div className="kpi-val" style={{ color: '#38bdf8' }}>{d.indemnityIndexCorrelation.toFixed(3)}</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">1-in-250 TVaR Saved</div>
              <div className="kpi-val" style={{ color: '#22c55e' }}>${d.tvar250ReductionM.toFixed(2)}m</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Hedge Effectiveness</div>
              <div className="kpi-val">{d.hedgeEffectivenessPct.toFixed(1)}%</div>
            </div>
            <div className="kpi">
              <div className="kpi-label">Cost / $ TVaR Saved</div>
              <div className="kpi-val">
                {d.costPerDollarTvarSaved != null ? `${(d.costPerDollarTvarSaved * 100).toFixed(1)}¢` : '—'}
              </div>
            </div>
          </div>
          <BreadcrumbBar manifest={d.manifest} />
        </>
      )}
    </div>
  );
}
