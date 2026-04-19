import { useState, useMemo, useEffect } from "react";

const USD_TO_CAD = 1.38;
const SCORED_PROPERTIES_URL = "../cache/scored_properties.json";
const SELECTED_RANK_STORAGE_KEY = "orlando_selected_rank";

const fmt = (n, currency) => {
  const cur = currency || "USD";
  return new Intl.NumberFormat("fr-CA", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(n);
};
const fmtK = (n) => {
  if (Math.abs(n) >= 1000) return (n / 1000).toFixed(0) + "K";
  return Math.round(n).toString();
};
const pct = (n) => n.toFixed(1) + "%";

const TRANCHES_QC = [
  { max: 51780,    fed: 0.205, prov: 0.14   },
  { max: 103545,   fed: 0.26,  prov: 0.19   },
  { max: 111733,   fed: 0.26,  prov: 0.24   },
  { max: 154906,   fed: 0.29,  prov: 0.2575 },
  { max: 220000,   fed: 0.33,  prov: 0.2575 },
  { max: Infinity, fed: 0.33,  prov: 0.2575 },
];

function getMarginalRate(income) {
  for (let i = 0; i < TRANCHES_QC.length; i++) {
    if (income <= TRANCHES_QC[i].max) return TRANCHES_QC[i].fed + TRANCHES_QC[i].prov;
  }
  return 0.5875;
}

function calcSolde(loan, mr, hypo, mois) {
  let s = loan;
  for (let i = 0; i < mois; i++) {
    s = s - (hypo - s * mr);
  }
  return Math.max(0, s);
}

function Slider(props) {
  const display = props.formatFn ? props.formatFn(props.value) : props.value;
  const w = ((props.value - props.min) / (props.max - props.min)) * 100;
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: "var(--fg-secondary)", textTransform: "uppercase", letterSpacing: "0.07em" }}>{props.label}</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: "var(--fg-primary)", fontFamily: "Georgia, serif" }}>{display}</span>
      </div>
      {props.sublabel && <div style={{ fontSize: 11, color: "var(--fg-dim)", marginBottom: 4 }}>{props.sublabel}</div>}
      <div style={{ position: "relative", height: 6, background: "var(--bg-chip)", borderRadius: 3 }}>
        <div style={{ position: "absolute", left: 0, top: 0, height: "100%", width: w + "%", background: "#2a9d8f", borderRadius: 3 }} />
        <input type="range" min={props.min} max={props.max} step={props.step || 1} value={props.value}
          onChange={(e) => props.onChange(Number(e.target.value))}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", opacity: 0, cursor: "pointer", margin: 0 }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 2 }}>
        <span style={{ fontSize: 10, color: "var(--fg-subtle)" }}>{props.formatFn ? props.formatFn(props.min) : props.min}</span>
        <span style={{ fontSize: 10, color: "var(--fg-subtle)" }}>{props.formatFn ? props.formatFn(props.max) : props.max}</span>
      </div>
    </div>
  );
}

function Card(props) {
  const ac = props.accent || "#2a9d8f";
  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid " + ac + "44", borderRadius: 14, padding: "16px 18px", marginBottom: 12 }}>
      {props.title && <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", color: ac, marginBottom: 10, fontWeight: 600 }}>{props.title}</div>}
      {props.children}
    </div>
  );
}

function Row(props) {
  const labelColor = props.highlight ? "var(--fg-primary)" : "var(--fg-muted)";
  const valColor = props.color || (props.highlight ? "var(--fg-primary)" : "var(--fg-secondary)");
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid var(--divider)" }}>
      <div style={{ flex: 1, marginRight: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 13, color: labelColor, fontWeight: props.highlight ? 600 : 400 }}>{props.label}</span>
          {props.info && <span style={{ fontSize: 9, background: "var(--bg-chip)", color: "#52b788", borderRadius: 3, padding: "1px 4px" }}>{props.info}</span>}
        </div>
        {props.sub && <div style={{ fontSize: 10, color: "var(--fg-faint)", marginTop: 1 }}>{props.sub}</div>}
      </div>
      <div style={{ fontSize: props.highlight ? 14 : 13, fontWeight: props.highlight ? 700 : 500, color: valColor, fontFamily: "Georgia, serif", whiteSpace: "nowrap" }}>{props.value}</div>
    </div>
  );
}

function Gauge(props) {
  const c = props.color || "#2a9d8f";
  const p = Math.min(Math.max(props.value / props.max, 0), 1) * 100;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span style={{ fontSize: 11, color: "var(--fg-muted)" }}>{props.label}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: c, fontFamily: "Georgia, serif" }}>{pct(p)}</span>
      </div>
      <div style={{ height: 5, background: "var(--bg-chip)", borderRadius: 3 }}>
        <div style={{ height: "100%", width: p + "%", background: c, borderRadius: 3 }} />
      </div>
    </div>
  );
}

function computeBase(prix, mf, tHypo, revBruts, tOcc, tGestion, assurance, maintenance, revPerso, tComVente, tAutresVente, hoa, canadaMontant, canadaTaux) {
  const down = prix * mf / 100;
  const loan = prix - down;
  const mr = tHypo / 100 / 12;
  const hypo = loan > 0 ? loan * (mr * Math.pow(1 + mr, 360)) / (Math.pow(1 + mr, 360) - 1) : 0;
  const taxesFonc = prix * 0.015 / 12;

  const revNet = revBruts * tOcc / 100;
  const gestion = revNet * tGestion / 100;
  const chargesOp = gestion + hoa + taxesFonc + assurance + maintenance;
  const cfBrut = revNet - chargesOp - hypo;
  const revAn = revNet * 12;
  const chargesAn = chargesOp * 12;
  const interets = loan * tHypo / 100 * 0.97;
  const amortUS = prix / 27.5;
  const imposUS = Math.max(0, revAn - chargesAn - interets - amortUS);
  const impotUS = imposUS * 0.22;
  const interetsCAD = interets * USD_TO_CAD;
  const revNetCAD = (revAn - chargesAn) * USD_TO_CAD;
  const imposCAD = Math.max(0, revNetCAD - interetsCAD);
  const txMarg = getMarginalRate(revPerso + imposCAD);
  const impotBrutCAD = imposCAD * txMarg;
  const creditT2209 = Math.min(impotUS * USD_TO_CAD, impotBrutCAD);
  const impotCAD = Math.max(0, impotBrutCAD - creditT2209);
  const canadaInteretsMensuel = canadaMontant > 0 ? canadaMontant * (canadaTaux / 100 / 12) : 0;
  const canadaInteretsAnnuel = canadaInteretsMensuel * 12;
  const cfNet = cfBrut - impotCAD / 12 / USD_TO_CAD - canadaInteretsMensuel / USD_TO_CAD;
  const cfNetSansCanada = cfBrut - impotCAD / 12 / USD_TO_CAD;
  const origination = loan * 0.0075;
  const titleIns = prix * 0.004;
  const docStampsHypo = loan * 0.0035;
  const fraisAchat = origination + 500 + 500 + 300 + titleIns + docStampsHypo + 400 + 500 + 300 + 1500;
  const coutEntree = down + fraisAchat;
  const capRate = (revAn - chargesAn) / prix * 100;
  const coc = down > 0 ? cfNet * 12 / down * 100 : 0;
  const grm = prix / (revNet * 12);
  return {
    down, loan, hypo, mr, taxesFonc, hoa, gestion, revNet, chargesOp, cfBrut, cfNet,
    revAn, chargesAn, interets, amortUS, imposUS, impotUS,
    revNetCAD, interetsCAD, imposCAD, txMarg, impotBrutCAD, creditT2209, impotCAD,
    capRate, coc, grm, coutEntree, fraisAchat, origination, titleIns, docStampsHypo,
    fraisAchatPct: fraisAchat / prix * 100,
    tComVente, tAutresVente,
    canadaInteretsMensuel, canadaInteretsAnnuel, cfNetSansCanada,
    coutEntreeTotal: coutEntree + canadaMontant * USD_TO_CAD,
  };
}

function computeAnnee(base, prix, annees, rendementETF, tTaxGain, tComVente, tAutresVente) {
  const { loan, mr, hypo, cfNet, coutEntree, txMarg } = base;
  const solde = calcSolde(loan, mr, hypo, annees * 12);
  const capRembourse = loan - solde;
  const valeur = prix * Math.pow(1.04, annees);
  const fraisVente = valeur * tComVente / 100 + valeur * 0.007 + 1500 + valeur * tAutresVente / 100;
  const gainCap = valeur - prix;
  const firpta = valeur * 0.15;
  const tg = (tTaxGain || 15) / 100;
  const impotGain = gainCap * tg;
  const rembFIRPTA = Math.max(0, firpta - impotGain);
  const cfCumule = cfNet * 12 * annees;
  const gainApprec = gainCap - impotGain;
  const gainNet = gainApprec + cfCumule - fraisVente;
  const equite = valeur - solde;
  const produitNet = valeur - solde - fraisVente - impotGain;
  const roi = coutEntree > 0 ? gainNet / coutEntree * 100 : 0;
  const roiAnnualise = coutEntree > 0 ? (Math.pow(1 + gainNet / coutEntree, 1 / annees) - 1) * 100 : 0;

  // ETF comparatif
  const capitalETF = coutEntree * USD_TO_CAD;
  const valETF = capitalETF * Math.pow(1 + rendementETF / 100, annees);
  const gainETF = valETF - capitalETF;
  const impotETF = gainETF * 0.5 * txMarg;
  const gainNetETF = gainETF - impotETF;
  const roiETF = gainNetETF / capitalETF * 100;
  const roiETFAnnualise = (Math.pow(1 + gainNetETF / capitalETF, 1 / annees) - 1) * 100;

  return {
    annees, solde, capRembourse, valeur, fraisVente, gainCap, firpta, impotGain,
    rembFIRPTA, cfCumule, gainApprec, gainNet, equite, produitNet, roi, roiAnnualise,
    capitalETF, valETF, gainETF, impotETF, gainNetETF, roiETF, roiETFAnnualise,
    avantageSurETF: gainNet * USD_TO_CAD - gainNetETF,
  };
}

function computeTimeline(base, prix, rendementETF, tTaxGain, tComVente, tAutresVente) {
  const points = [];
  for (let y = 1; y <= 20; y++) {
    const d = computeAnnee(base, prix, y, rendementETF, tTaxGain, tComVente, tAutresVente);
    points.push(d);
  }
  return points;
}

function MiniChart(props) {
  const { data, selectedYear } = props;
  const immoVals = data.map((d) => d.gainNet * USD_TO_CAD);
  const etfVals = data.map((d) => d.gainNetETF);
  const allVals = [...immoVals, ...etfVals];
  const minVal = Math.min(...allVals);
  const maxVal = Math.max(...allVals);
  const range = maxVal - minVal || 1;
  const W = 320;
  const H = 160;
  const PAD = { top: 16, right: 16, bottom: 28, left: 48 };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top - PAD.bottom;
  const years = data.length;

  const toX = (i) => PAD.left + (i / (years - 1)) * chartW;
  const toY = (v) => PAD.top + chartH - ((v - minVal) / range) * chartH;

  const immoPath = immoVals.map((v, i) => (i === 0 ? "M" : "L") + toX(i).toFixed(1) + "," + toY(v).toFixed(1)).join(" ");
  const etfPath = etfVals.map((v, i) => (i === 0 ? "M" : "L") + toX(i).toFixed(1) + "," + toY(v).toFixed(1)).join(" ");

  const selIdx = selectedYear - 1;
  const selX = toX(selIdx);
  const selImmoY = toY(immoVals[selIdx]);
  const selEtfY = toY(etfVals[selIdx]);

  const yLabels = [minVal, minVal + range * 0.5, maxVal];

  return (
    <div style={{ overflowX: "auto" }}>
      <svg width={W} height={H} style={{ display: "block", margin: "0 auto" }}>
        {yLabels.map((v, i) => {
          const y = toY(v);
          return (
            <g key={i}>
              <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y} stroke="var(--bg-chip)" strokeWidth="1" />
              <text x={PAD.left - 4} y={y + 4} textAnchor="end" fontSize="9" fill="var(--fg-faint)">{fmtK(v)}</text>
            </g>
          );
        })}
        {[0, 4, 9, 14, 19].map((i) => (
          <text key={i} x={toX(i)} y={H - 4} textAnchor="middle" fontSize="9" fill="var(--fg-faint)">{i + 1}a</text>
        ))}
        <path d={etfPath} fill="none" stroke="#3d7fbf" strokeWidth="2" strokeDasharray="4,3" />
        <path d={immoPath} fill="none" stroke="#52b788" strokeWidth="2.5" />
        <line x1={selX} y1={PAD.top} x2={selX} y2={H - PAD.bottom} stroke="var(--fg-primary)" strokeWidth="1" strokeDasharray="3,2" />
        <circle cx={selX} cy={selImmoY} r="4" fill="#52b788" />
        <circle cx={selX} cy={selEtfY} r="4" fill="#3d7fbf" />
        <text x={W - PAD.right} y={PAD.top + 10} textAnchor="end" fontSize="9" fill="#52b788">Immo</text>
        <text x={W - PAD.right} y={PAD.top + 22} textAnchor="end" fontSize="9" fill="#3d7fbf">ETF</text>
      </svg>
    </div>
  );
}

function PropertySelector(props) {
  const { properties, selectedIdx, onSelect } = props;
  const [open, setOpen] = useState(false);
  const current = properties[selectedIdx];
  if (!current) return null;

  const go = (delta) => {
    const n = properties.length;
    onSelect(((selectedIdx + delta) % n + n) % n);
  };

  return (
    <div style={{ position: "relative", marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--bg-soft)", border: "1px solid var(--bg-chip)", borderRadius: 12, padding: "10px 12px" }}>
        <button onClick={() => go(-1)}
          style={{ background: "var(--bg-card)", border: "1px solid var(--bg-chip)", color: "var(--fg-secondary)", width: 32, height: 32, borderRadius: 8, cursor: "pointer", fontSize: 14 }}>◀</button>
        <div onClick={() => setOpen(!open)} style={{ flex: 1, cursor: "pointer" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div>
              <span style={{ fontSize: 10, letterSpacing: "0.15em", color: "#2a9d8f", textTransform: "uppercase", fontWeight: 600 }}>
                #{current.rank} / {properties.length}
              </span>
              {current.detail_is_new && (
                <span style={{ marginLeft: 8, fontSize: 9, color: "#ffd166", background: "var(--bg-warning)", padding: "1px 6px", borderRadius: 8, letterSpacing: "0.1em", fontWeight: 700 }}>✨ NEW</span>
              )}
              {current.is_price_reduced && (
                <span style={{ marginLeft: 6, fontSize: 9, color: "#e07070", background: "var(--bg-danger-deep)", padding: "1px 6px", borderRadius: 8, letterSpacing: "0.1em", fontWeight: 700 }}>↓ PRICE</span>
              )}
            </div>
            <span style={{ fontSize: 11, color: "var(--fg-dim)" }}>{open ? "▲" : "▼"}</span>
          </div>
          <div style={{ fontSize: 13, color: "var(--fg-primary)", marginTop: 2, fontFamily: "Georgia, serif", fontWeight: 600 }}>
            {current.resort_name && current.resort_name !== "Unknown Resort" ? current.resort_name : "Propriété"}
          </div>
          <div style={{ fontSize: 10, color: "var(--fg-dim)", marginTop: 1 }}>
            {fmt(current.price)} · {current.bedrooms}BR/{current.bathrooms}BA · Score {(current.investment_score || 0).toFixed(1)}/10
          </div>
        </div>
        <button onClick={() => go(1)}
          style={{ background: "var(--bg-card)", border: "1px solid var(--bg-chip)", color: "var(--fg-secondary)", width: 32, height: 32, borderRadius: 8, cursor: "pointer", fontSize: 14 }}>▶</button>
      </div>

      {open && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, marginTop: 4, background: "var(--bg-soft)", border: "1px solid #2a9d8f44", borderRadius: 12, zIndex: 10, maxHeight: 360, overflowY: "auto", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}>
          {properties.map((p, i) => (
            <div key={p.id || i}
              onClick={() => { onSelect(i); setOpen(false); }}
              style={{ padding: "10px 14px", borderBottom: "1px solid var(--divider)", cursor: "pointer", background: i === selectedIdx ? "var(--bg-success)" : "transparent" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 11, color: "#2a9d8f", fontWeight: 700 }}>#{p.rank}</span>
                <span style={{ fontSize: 11, color: "var(--fg-secondary)", fontFamily: "Georgia, serif" }}>{fmt(p.price)}</span>
              </div>
              <div style={{ fontSize: 12, color: "var(--fg-primary)", marginTop: 2 }}>
                {p.resort_name && p.resort_name !== "Unknown Resort" ? p.resort_name : p.address.split(",")[0]}
              </div>
              <div style={{ fontSize: 10, color: "var(--fg-dim)", marginTop: 1 }}>
                {p.bedrooms}BR · {p.property_type} · Score {(p.investment_score || 0).toFixed(1)}
                {p.detail_is_new && <span style={{ color: "#ffd166", marginLeft: 6 }}>✨ NEW</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ThemeToggle(props) {
  const { theme, setTheme } = props;
  const isDark = theme === "dark";
  return (
    <button
      onClick={() => setTheme(isDark ? "light" : "dark")}
      title={isDark ? "Passer en thème clair" : "Passer en thème sombre"}
      aria-label="Changer de thème"
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--bg-chip)",
        color: "var(--fg-secondary)",
        width: 40,
        height: 40,
        borderRadius: 10,
        cursor: "pointer",
        fontSize: 16,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}>
      {isDark ? "☀️" : "🌙"}
    </button>
  );
}

function DiscoveryTab(props) {
  const { p } = props;
  const [descOpen, setDescOpen] = useState(false);
  const strSignals = [];
  if (p.resort_name && p.resort_name !== "Unknown Resort") strSignals.push("Communauté: " + p.resort_name);
  if (p.str_keywords_found && p.str_keywords_found.length) {
    strSignals.push("Mots-clés: " + p.str_keywords_found.slice(0, 5).join(", "));
  }
  if (p.zip_used) strSignals.push("Zip " + p.zip_used + " (corridor STR)");

  return (
    <div>
      {strSignals.length > 0 && (
        <Card title="Signaux STR" accent="#52b788">
          {strSignals.map((s, i) => (
            <div key={i} style={{ padding: "5px 0", fontSize: 12, color: "var(--fg-secondary)" }}>• {s}</div>
          ))}
        </Card>
      )}

      <Card title={"Score STR : " + (p.investment_score || 0).toFixed(1) + " / 10"} accent="#2a9d8f">
        <Row label="Type" value={p.property_type + " · " + p.bedrooms + "BR/" + p.bathrooms + "BA"} />
        <Row label="Surface" value={(p.square_feet || 0).toLocaleString() + " sqft"} />
        {p.year_built && <Row label="Année construction" value={String(p.year_built)} />}
        {p.days_on_market !== null && p.days_on_market !== undefined && (
          <Row label="Jours sur marché" value={String(p.days_on_market)} color={p.days_on_market > 90 ? "#e07070" : "#52b788"} />
        )}
        <Row label="Prix / sqft" value={p.price_per_sqft ? fmt(p.price_per_sqft) : fmt(p.price / Math.max(p.square_feet, 1))} />
        {p.pool !== null && p.pool !== undefined && (
          <Row label="Piscine" value={p.pool ? "Oui" : "Non"} color={p.pool ? "#52b788" : "var(--fg-dim)"} />
        )}
        {p.flood_risk && <Row label="Risque inondation" value={p.flood_risk} color={p.flood_risk === "minimal" ? "#52b788" : "#d4a017"} />}
        {p.parking && <Row label="Stationnement" value={p.parking} />}
      </Card>

      {p.negative_flags && p.negative_flags.length > 0 && (
        <Card title="Drapeaux rouges" accent="#e07070">
          {p.negative_flags.map((f, i) => (
            <div key={i} style={{ padding: "5px 0", fontSize: 12, color: "#e07070" }}>⚠️ {f}</div>
          ))}
        </Card>
      )}

      <Card title="Badges" accent="#d4a017">
        {p.detail_is_new && <Row label="✨ Nouvelle annonce" value="Détectée récemment" color="#ffd166" />}
        {p.is_new_listing && <Row label="🆕 Listing récent" value="MLS 'new'" color="#52b788" />}
        {p.is_price_reduced && <Row label="↓ Prix réduit" value="Baisse récente" color="#e07070" />}
        {p.has_matterport && <Row label="📸 Tour Matterport 3D" value="Disponible" color="#52b788" />}
        {p.virtual_tours_count > 0 && <Row label="🎥 Visite virtuelle" value={p.virtual_tours_count + " tour(s)"} color="#52b788" />}
        {p.photo_count > 0 && <Row label="📷 Photos" value={String(p.photo_count)} />}
      </Card>

      <Card title="HOA / Impôts fonciers" accent="#3d7fbf">
        <Row label="HOA mensuel" value={p.hoa_fee_monthly !== null && p.hoa_fee_monthly !== undefined ? fmt(p.hoa_fee_monthly) : "Non listé"}
          color={p.hoa_fee_monthly > 500 ? "#e07070" : p.hoa_fee_monthly < 300 ? "#52b788" : "var(--fg-secondary)"} />
        {p.hoa_includes && p.hoa_includes.length > 0 && (
          <Row label="HOA inclut" value={p.hoa_includes.join(", ")} />
        )}
        <Row label="Impôt foncier / an" value={p.annual_tax ? fmt(p.annual_tax) : "Non listé"} />
        {p.tax_year && <Row label="Année impôt" value={String(p.tax_year)} />}
      </Card>

      <Card title="Estimation revenus AirDNA" accent="#d4a017">
        <Row label="ADR médian estimé" value={fmt(p.estimated_nightly) + " / nuit"} color="#52b788" />
        <Row label="Occupation estimée" value={pct(p.estimated_occupancy * 100)} color="#52b788" />
        <Row label="Revenus mensuels bruts" value={fmt(p.estimated_monthly_gross)} highlight color="#d4a017" />
        <Row label="Source" value={p.revenue_estimate_source.replace(/_/g, " ")} sub="airdna_zip = données par code postal · bedroom_fallback = fallback générique" />
      </Card>

      {p.full_description && (
        <Card title="Description complète" accent="var(--fg-dim)">
          <div onClick={() => setDescOpen(!descOpen)}
            style={{ cursor: "pointer", fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.6, maxHeight: descOpen ? "none" : 80, overflow: "hidden", position: "relative" }}>
            {p.full_description}
            {!descOpen && <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 30, background: "linear-gradient(transparent, var(--bg-card))" }} />}
          </div>
          <div onClick={() => setDescOpen(!descOpen)} style={{ textAlign: "center", fontSize: 11, color: "#2a9d8f", marginTop: 8, cursor: "pointer" }}>
            {descOpen ? "▲ Réduire" : "▼ Voir plus"}
          </div>
        </Card>
      )}

      <Card title="Source de l'annonce" accent="#3d7fbf">
        <div style={{ fontSize: 11, color: "var(--fg-dim)", marginBottom: 6 }}>{p.address}</div>
        <a href={p.listing_url} target="_blank" rel="noopener noreferrer"
          style={{ display: "block", textAlign: "center", padding: "10px 14px", background: "var(--bg-success)", border: "1px solid #2a9d8f", borderRadius: 8, color: "#2a9d8f", textDecoration: "none", fontSize: 13, fontWeight: 600 }}>
          🌐 Voir l'annonce Realtor.com ↗
        </a>
      </Card>
    </div>
  );
}

export default function App() {
  // Property list loaded from cache/scored_properties.json
  const [properties, setProperties] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [meta, setMeta] = useState(null);
  const [selectedIdx, setSelectedIdx] = useState(() => {
    const saved = parseInt(localStorage.getItem(SELECTED_RANK_STORAGE_KEY) || "0", 10);
    return isNaN(saved) ? 0 : saved;
  });

  // Theme (light = default). Initialised from the data-theme attribute
  // that the pre-mount script already set, so we stay in sync.
  const [theme, setTheme] = useState(() => {
    if (typeof document !== "undefined") {
      return document.documentElement.getAttribute("data-theme") || "light";
    }
    return "light";
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch (e) {}
  }, [theme]);

  useEffect(() => {
    fetch(SCORED_PROPERTIES_URL)
      .then(r => r.ok ? r.json() : Promise.reject("HTTP " + r.status))
      .then(data => {
        setProperties(data.properties || []);
        setMeta({ generated_at: data.generated_at, total_api_calls: data.total_api_calls, local_mode: data.local_mode, new_window: data.new_badge_window_days });
        // Clamp selectedIdx to available range
        if (selectedIdx >= (data.properties || []).length) setSelectedIdx(0);
      })
      .catch(err => setLoadError(String(err)));
  }, []);

  useEffect(() => {
    localStorage.setItem(SELECTED_RANK_STORAGE_KEY, String(selectedIdx));
  }, [selectedIdx]);

  // Keyboard navigation: arrow keys to switch properties
  useEffect(() => {
    const handler = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "ArrowLeft" && properties.length) {
        setSelectedIdx(i => ((i - 1) % properties.length + properties.length) % properties.length);
      } else if (e.key === "ArrowRight" && properties.length) {
        setSelectedIdx(i => (i + 1) % properties.length);
      } else if (e.key >= "1" && e.key <= "9" && properties.length) {
        const n = parseInt(e.key, 10) - 1;
        if (n < properties.length) setSelectedIdx(n);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [properties.length]);

  const selectedProp = properties[selectedIdx] || null;

  // Sliders with per-property defaults
  const [prix, setPrix] = useState(320000);
  const [mf, setMf] = useState(30);
  const [tHypo, setTHypo] = useState(7.5);
  const [revBruts, setRevBruts] = useState(4200);
  const [tOcc, setTOcc] = useState(68);
  const [tGestion, setTGestion] = useState(20);
  const [assurance, setAssurance] = useState(180);
  const [maintenance, setMaintenance] = useState(150);
  const [revPerso, setRevPerso] = useState(120000);
  const [tComVente, setTComVente] = useState(5.5);
  const [tAutresVente, setTAutresVente] = useState(1.5);
  const [rendementETF, setRendementETF] = useState(7);
  const [hoa, setHoa] = useState(300);
  const [tTaxGain, setTTaxGain] = useState(15);
  const [canadaActive, setCanadaActive] = useState(false);
  const [canadaMontant, setCanadaMontant] = useState(50000);
  const [canadaTaux, setCanadaTaux] = useState(6.5);
  const [horizon, setHorizon] = useState(7);
  const [tab, setTab] = useState("decouverte");

  // When selected property changes, reset ONLY price / HOA / revenue.
  // User-tuned params (rate, occupancy, management...) are preserved.
  useEffect(() => {
    if (!selectedProp) return;
    if (selectedProp.price) setPrix(selectedProp.price);
    if (selectedProp.hoa_fee_monthly !== null && selectedProp.hoa_fee_monthly !== undefined) {
      setHoa(Math.round(selectedProp.hoa_fee_monthly));
    }
    if (selectedProp.estimated_monthly_gross) {
      setRevBruts(Math.round(selectedProp.estimated_monthly_gross));
    }
    if (selectedProp.estimated_occupancy) {
      setTOcc(Math.round(selectedProp.estimated_occupancy * 100));
    }
  }, [selectedIdx, selectedProp]);

  const base = useMemo(() =>
    computeBase(prix, mf, tHypo, revBruts, tOcc, tGestion, assurance, maintenance, revPerso, tComVente, tAutresVente, hoa, canadaActive ? canadaMontant : 0, canadaTaux),
    [prix, mf, tHypo, revBruts, tOcc, tGestion, assurance, maintenance, revPerso, tComVente, tAutresVente, hoa, canadaActive, canadaMontant, canadaTaux]
  );

  const sel = useMemo(() => computeAnnee(base, prix, horizon, rendementETF, tTaxGain, tComVente, tAutresVente), [base, prix, horizon, rendementETF, tTaxGain, tComVente, tAutresVente]);
  const timeline = useMemo(() => computeTimeline(base, prix, rendementETF, tTaxGain, tComVente, tAutresVente), [base, prix, rendementETF, tTaxGain, tComVente, tAutresVente]);

  const an5 = useMemo(() => computeAnnee(base, prix, 5, rendementETF, tTaxGain, tComVente, tAutresVente), [base, prix, rendementETF, tTaxGain, tComVente, tAutresVente]);
  const an10 = useMemo(() => computeAnnee(base, prix, 10, rendementETF, tTaxGain, tComVente, tAutresVente), [base, prix, rendementETF, tTaxGain, tComVente, tAutresVente]);

  const isPos = base.cfNet >= 0;
  const cfColor = isPos ? "#52b788" : "#e07070";
  const heroBg = isPos ? "var(--bg-success)" : "var(--bg-danger)";

  const tabs = [
    ["decouverte","Découverte"],["horizon","Horizon"],["mensuel","Mensuel"],["frais","Frais"],
    ["annuel","Annuel"],["canada","Emprunt CA"],["etf","ETF vs Immo"]
  ];

  // Trouver l'année où immo dépasse ETF
  const crossoverYear = timeline.findIndex((d) => d.avantageSurETF > 0) + 1;

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-app)", padding: "18px 14px", fontFamily: "system-ui, sans-serif" }}>

      <div style={{ textAlign: "center", marginBottom: 14 }}>
        <div style={{ fontSize: 10, letterSpacing: "0.2em", color: "#2a9d8f", textTransform: "uppercase", marginBottom: 4 }}>Analyse d'investissement</div>
        <h1 style={{ fontFamily: "Georgia, serif", fontSize: 22, color: "var(--fg-primary)", margin: 0 }}>
          {selectedProp
            ? (selectedProp.resort_name && selectedProp.resort_name !== "Unknown Resort"
                ? selectedProp.resort_name
                : (selectedProp.address || "").split(",")[0])
            : "Orlando STR"}
        </h1>
        <div style={{ fontSize: 11, color: "var(--fg-faint)", marginTop: 3 }}>
          {selectedProp ? (selectedProp.address || "").split(",").slice(1).join(",").trim() : "Kissimmee / Davenport / I-Drive"}
          {" · Investisseur Québécois · T776 + Frais FL"}
        </div>
      </div>

      {/* Property selector — only shown if data loaded */}
      {loadError && (
        <div style={{ background: "var(--bg-danger)", border: "1px solid #e0707044", borderRadius: 12, padding: 14, marginBottom: 14, textAlign: "center" }}>
          <div style={{ fontSize: 12, color: "#e07070", marginBottom: 4 }}>⚠️ Aucune donnée de recherche trouvée</div>
          <div style={{ fontSize: 11, color: "var(--fg-muted)", lineHeight: 1.6 }}>
            Exécute d'abord <code style={{ background: "var(--bg-soft)", padding: "1px 6px", borderRadius: 3, color: "var(--fg-secondary)" }}>python3 src/property_finder.py</code> pour générer <code style={{ background: "var(--bg-soft)", padding: "1px 6px", borderRadius: 3, color: "var(--fg-secondary)" }}>cache/scored_properties.json</code>
          </div>
        </div>
      )}
      {properties.length > 0 && (
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <PropertySelector properties={properties} selectedIdx={selectedIdx} onSelect={setSelectedIdx} />
          </div>
          <div style={{ marginBottom: 14 }}>
            <ThemeToggle theme={theme} setTheme={setTheme} />
          </div>
        </div>
      )}
      {properties.length === 0 && !loadError && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
          <ThemeToggle theme={theme} setTheme={setTheme} />
        </div>
      )}

      {/* Tab nav */}
      <div style={{ display: "flex", justifyContent: "center", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
        {tabs.map((item) => (
          <button key={item[0]} onClick={() => setTab(item[0])}
            style={{ padding: "4px 12px", borderRadius: 20, border: tab === item[0] ? "1px solid #2a9d8f" : "1px solid var(--bg-chip)", background: tab === item[0] ? "var(--bg-success)" : "transparent", color: tab === item[0] ? "#2a9d8f" : "var(--fg-faint)", fontSize: 11, cursor: "pointer" }}>
            {item[1]}
          </button>
        ))}
      </div>

      {/* Hero */}
      <div style={{ background: heroBg, border: "1px solid " + cfColor + "44", borderRadius: 18, padding: 16, marginBottom: 16, textAlign: "center" }}>
        <div style={{ display: "flex", justifyContent: "space-around", alignItems: "center" }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.1em", color: cfColor, marginBottom: 3 }}>Cash Flow/mois</div>
            <div style={{ fontSize: 28, fontFamily: "Georgia, serif", fontWeight: 700, color: cfColor }}>{fmt(base.cfNet)}</div>
            <div style={{ fontSize: 10, color: "var(--fg-faint)" }}>{fmt(base.cfNet * USD_TO_CAD, "CAD")}</div>
          </div>
          <div style={{ width: 1, height: 60, background: "var(--bg-chip)" }} />
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.1em", color: "#52b788", marginBottom: 3 }}>{"ROI an " + horizon + " (revente)"}</div>
            <div style={{ fontSize: 28, fontFamily: "Georgia, serif", fontWeight: 700, color: sel.roi >= 0 ? "#52b788" : "#e07070" }}>{pct(sel.roi)}</div>
            <div style={{ fontSize: 10, color: "var(--fg-faint)" }}>{pct(sel.roiAnnualise) + " annualise"}</div>
          </div>
        </div>
        <div style={{ display: "flex", justifyContent: "center", gap: 20, marginTop: 12 }}>
          {[
            ["Cap Rate", pct(base.capRate)],
            ["Gain net CAD", fmt(sel.gainNet * USD_TO_CAD, "CAD")],
            ["vs ETF", sel.avantageSurETF >= 0 ? "+" + fmt(sel.avantageSurETF, "CAD") : fmt(sel.avantageSurETF, "CAD")]
          ].map((item) => (
            <div key={item[0]} style={{ textAlign: "center" }}>
              <div style={{ fontSize: 12, fontFamily: "Georgia, serif", color: "var(--fg-primary-alt)", fontWeight: 600 }}>{item[1]}</div>
              <div style={{ fontSize: 10, color: "var(--fg-faint)" }}>{item[0]}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Paramètres */}
      <Card title="Parametres" accent="#3d7fbf">
        <Slider label="Prix d'achat" value={prix} min={250000} max={500000} step={5000} onChange={setPrix} formatFn={fmt} />
        <Slider label="Mise de fonds" value={mf} min={25} max={60} step={5} onChange={setMf}
          formatFn={(v) => v + "% - " + fmt(prix * v / 100)}
          sublabel={"Emprunt : " + fmt(prix * (1 - mf / 100))} />
        <Slider label="Taux hypothecaire" value={tHypo} min={5} max={10} step={0.25} onChange={setTHypo}
          formatFn={(v) => v + "%"} sublabel="Pret non-warrantable" />
        <Slider label="Revenus Airbnb bruts/mois" value={revBruts} min={2000} max={7000} step={100} onChange={setRevBruts} formatFn={fmt} />
        <Slider label="Taux d'occupation" value={tOcc} min={40} max={90} step={1} onChange={setTOcc} formatFn={(v) => v + "%"} />
        <Slider label="Gestion locative" value={tGestion} min={10} max={30} step={1} onChange={setTGestion} formatFn={(v) => v + "%"} />
        <Slider label="Assurance/mois" value={assurance} min={100} max={400} step={10} onChange={setAssurance} formatFn={fmt} />
        <Slider label="Maintenance/mois" value={maintenance} min={50} max={400} step={25} onChange={setMaintenance} formatFn={fmt} />
        <Slider label="Revenu personnel CAD" value={revPerso} min={60000} max={250000} step={5000} onChange={setRevPerso}
          formatFn={(v) => fmt(v, "CAD")} sublabel="Tranche marginale quebecoise" />
        <Slider label="Commission agent vente" value={tComVente} min={3} max={7} step={0.5} onChange={setTComVente} formatFn={(v) => v + "%"} />
        <Slider label="Autres frais vente" value={tAutresVente} min={0.5} max={3} step={0.5} onChange={setTAutresVente} formatFn={(v) => v + "%"} />
        <Slider label="Rendement ETF annuel" value={rendementETF} min={4} max={12} step={0.5} onChange={setRendementETF}
          formatFn={(v) => v + "%"} sublabel="Pour le comparatif ETF vs immobilier" />
        <Slider label="HOA mensuel" value={hoa} min={149} max={1200} step={25} onChange={setHoa}
          formatFn={(v) => fmt(v)} sublabel="Terra Verde officiel: 149$/mois — condos resort haut de gamme peuvent atteindre ~1200$/mois" />
        <Slider label="Impot gain capital US" value={tTaxGain} min={0} max={20} step={5} onChange={setTTaxGain}
          formatFn={(v) => v + "%"} sublabel="0% (petit gain) / 15% (typique) / 20% (gain eleve)" />
      </Card>

      {/* DÉCOUVERTE */}
      {tab === "decouverte" && selectedProp && (
        <DiscoveryTab p={selectedProp} />
      )}
      {tab === "decouverte" && !selectedProp && (
        <Card title="Aucune propriété chargée" accent="#e07070">
          <div style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.8 }}>
            La cache de recherche est vide. Exécute le script Python d'abord :
            <div style={{ background: "var(--bg-soft)", padding: "8px 10px", borderRadius: 6, marginTop: 8, fontFamily: "monospace", fontSize: 11, color: "var(--fg-secondary)" }}>
              python3 src/property_finder.py
            </div>
          </div>
        </Card>
      )}

      {/* HORIZON */}
      {tab === "horizon" && (
        <div>
          <Card title="Horizon de revente" accent="#2a9d8f">
            <Slider label="Annee de revente" value={horizon} min={1} max={20} step={1} onChange={setHorizon}
              formatFn={(v) => "An " + v}
              sublabel={crossoverYear > 0 ? "Immo depasse ETF a partir de l'an " + crossoverYear : "Immo toujours en avance"} />
          </Card>

          <div style={{ background: "var(--bg-card)", border: "1px solid #2a9d8f44", borderRadius: 14, padding: "16px 18px", marginBottom: 12 }}>
            <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.1em", color: "#2a9d8f", marginBottom: 10, fontWeight: 600 }}>Gain net CAD — Immo vs ETF</div>
            <MiniChart data={timeline} selectedYear={horizon} />
            <div style={{ display: "flex", justifyContent: "center", gap: 20, marginTop: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <div style={{ width: 16, height: 2, background: "#52b788" }} />
                <span style={{ fontSize: 10, color: "#52b788" }}>Immobilier</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <div style={{ width: 16, height: 2, background: "#3d7fbf", borderTop: "2px dashed #3d7fbf" }} />
                <span style={{ fontSize: 10, color: "#3d7fbf" }}>ETF {rendementETF}%</span>
              </div>
            </div>
          </div>

          <Card title={"Bilan an " + horizon + " — Immobilier"} accent="#52b788">
            <Row label="Valeur estimee" value={fmt(sel.valeur)} color="#52b788" sub={"Prix x (1.04)^" + horizon} />
            <Row label="Solde restant du pret" value={fmt(sel.solde)} color="#e07070" />
            <Row label="Capital rembourse" value={fmt(sel.capRembourse)} color="#52b788" />
            <Row label="Equite brute" value={fmt(sel.equite)} color="#52b788" sub="Valeur - solde pret" />
            <Row label="Gain en capital brut" value={fmt(sel.gainCap)} color="#52b788" />
            <Row label={"Impot gain US (" + tTaxGain + "%)"} value={"-" + fmt(sel.impotGain)} color="#e07070" />
            <Row label="Frais de vente" value={"-" + fmt(sel.fraisVente)} color="#e07070" />
            <Row label="Cash flow cumule" value={fmt(sel.cfCumule)} color={sel.cfCumule >= 0 ? "#52b788" : "#e07070"} />
            <Row label="Gain net total" value={fmt(sel.gainNet)} highlight color={sel.gainNet >= 0 ? "#52b788" : "#e07070"} />
            <Row label="Produit net en poche" value={fmt(sel.produitNet)} highlight color="#52b788" sub="Apres pret + frais + impot" />
            <Row label="Gain net CAD" value={fmt(sel.gainNet * USD_TO_CAD, "CAD")} color={sel.gainNet >= 0 ? "#52b788" : "#e07070"} />
            <Row label="ROI total" value={pct(sel.roi)} highlight color={sel.roi >= 0 ? "#52b788" : "#e07070"} />
            <Row label="ROI annualise" value={pct(sel.roiAnnualise)} color={sel.roiAnnualise >= 0 ? "#52b788" : "#e07070"} />
          </Card>

          <Card title={"Bilan an " + horizon + " — ETF " + rendementETF + "%"} accent="#3d7fbf">
            <Row label="Capital investi" value={fmt(sel.capitalETF, "CAD")} sub="Cout entree total en CAD" />
            <Row label="Valeur finale brute" value={fmt(sel.valETF, "CAD")} color="#3d7fbf" />
            <Row label="Gain brut" value={fmt(sel.gainETF, "CAD")} color="#3d7fbf" />
            <Row label="Impot gain capital QC" value={"-" + fmt(sel.impotETF, "CAD")} color="#e07070" sub="Inclusion 50% x taux marginal" />
            <Row label="Gain net apres impot" value={fmt(sel.gainNetETF, "CAD")} highlight color="#3d7fbf" />
            <Row label="ROI total" value={pct(sel.roiETF)} highlight color="#3d7fbf" />
            <Row label="ROI annualise" value={pct(sel.roiETFAnnualise)} color="#3d7fbf" />
          </Card>

          <Card title="Verdict" accent={sel.avantageSurETF >= 0 ? "#52b788" : "#3d7fbf"}>
            <Row label="Gain immo CAD" value={fmt(sel.gainNet * USD_TO_CAD, "CAD")} color="#52b788" />
            <Row label="Gain ETF CAD" value={fmt(sel.gainNetETF, "CAD")} color="#3d7fbf" />
            <Row label={sel.avantageSurETF >= 0 ? "Avantage immobilier" : "Avantage ETF"}
              value={fmt(Math.abs(sel.avantageSurETF), "CAD")}
              highlight color={sel.avantageSurETF >= 0 ? "#52b788" : "#3d7fbf"} />
            {crossoverYear > 0 && crossoverYear <= 20 && (
              <div style={{ fontSize: 11, color: "var(--fg-dim)", marginTop: 8, lineHeight: 1.6 }}>
                L'immobilier devient plus avantageux que l'ETF a partir de l'an {crossoverYear} avec les parametres actuels.
              </div>
            )}
          </Card>

          <Card title="Tableau annee par annee" accent="var(--fg-dim)">
            <div style={{ display: "grid", gridTemplateColumns: "0.6fr 1fr 1fr 1fr", gap: 2, marginBottom: 6 }}>
              <span style={{ fontSize: 10, color: "var(--fg-faint)" }}>An</span>
              <span style={{ fontSize: 10, color: "#52b788", textAlign: "right" }}>Gain immo CAD</span>
              <span style={{ fontSize: 10, color: "#3d7fbf", textAlign: "right" }}>Gain ETF CAD</span>
              <span style={{ fontSize: 10, color: "var(--fg-secondary)", textAlign: "right" }}>Avantage</span>
            </div>
            {timeline.map((d) => {
              const isSelected = d.annees === horizon;
              const immoGain = d.gainNet * USD_TO_CAD;
              const avantage = d.avantageSurETF;
              return (
                <div key={d.annees}
                  onClick={() => setHorizon(d.annees)}
                  style={{ display: "grid", gridTemplateColumns: "0.6fr 1fr 1fr 1fr", padding: "5px 0", borderBottom: "1px solid var(--divider)", background: isSelected ? "var(--bg-success)" : "transparent", cursor: "pointer", borderRadius: 4 }}>
                  <span style={{ fontSize: 12, color: isSelected ? "#2a9d8f" : "var(--fg-dim)", fontWeight: isSelected ? 700 : 400 }}>{"An " + d.annees}</span>
                  <span style={{ fontSize: 12, color: "#52b788", fontFamily: "Georgia, serif", textAlign: "right" }}>{fmt(immoGain, "CAD")}</span>
                  <span style={{ fontSize: 12, color: "#3d7fbf", fontFamily: "Georgia, serif", textAlign: "right" }}>{fmt(d.gainNetETF, "CAD")}</span>
                  <span style={{ fontSize: 12, color: avantage >= 0 ? "#52b788" : "#3d7fbf", fontFamily: "Georgia, serif", textAlign: "right" }}>{avantage >= 0 ? "+" : ""}{fmt(avantage, "CAD")}</span>
                </div>
              );
            })}
          </Card>
        </div>
      )}

      {/* MENSUEL */}
      {tab === "mensuel" && (
        <div>
          <Card title="Revenus mensuels" accent="#52b788">
            <Row label="Revenus bruts Airbnb" value={fmt(revBruts)} />
            <Row label={"Inoccupation (" + (100 - tOcc) + "%)"} value={"-" + fmt(revBruts - base.revNet)} color="#e07070" />
            <Row label="Revenus effectifs" value={fmt(base.revNet)} highlight />
          </Card>
          <Card title="Charges mensuelles" accent="#e07070">
            <Row label="HOA Terra Verde" value={fmt(base.hoa)} sub="149 $/mois officiel" />
            <Row label="Taxes foncieres FL (1.5%)" value={fmt(base.taxesFonc)} />
            <Row label="Assurance" value={fmt(assurance)} />
            <Row label={"Gestion (" + tGestion + "%)"} value={fmt(base.gestion)} />
            <Row label="Maintenance" value={fmt(maintenance)} />
            <Row label="Total operationnel" value={fmt(base.chargesOp)} highlight />
            <Row label="Hypotheque" value={fmt(base.hypo)} sub={fmt(base.loan) + " @ " + tHypo + "% / 30 ans"} />
            <Row label="Cash flow avant impots" value={fmt(base.cfBrut)} highlight color={base.cfBrut >= 0 ? "#52b788" : "#e07070"} />
          </Card>
          <Card title="Fiscalite T776" accent="#d4a017">
            <Row label="Revenus nets CAD" value={fmt(base.revNetCAD, "CAD")} sub="(Revenus - charges) x 1.38" />
            <Row label="Interets hypothecaires" value={"-" + fmt(base.interetsCAD, "CAD")} color="#52b788" info="T776" />
            <Row label="Revenu imposable CRA" value={fmt(base.imposCAD, "CAD")} highlight />
            <Row label="Taux marginal QC" value={pct(base.txMarg * 100)} />
            <Row label="Impot brut" value={fmt(base.impotBrutCAD, "CAD")} />
            <Row label="Credit T2209" value={"-" + fmt(base.creditT2209, "CAD")} color="#52b788" info="T2209" />
            <Row label="Impot net Canada/QC" value={fmt(base.impotCAD, "CAD")} highlight color="#d4a017" />
            <Row label="Impact mensuel USD" value={"-" + fmt(base.impotCAD / 12 / USD_TO_CAD)} color="#e07070" />
          </Card>
          <div style={{ background: "var(--bg-elevated)", border: "1px solid #2a9d8f22", borderRadius: 14, padding: 14, marginBottom: 12 }}>
            <Gauge label="Charges totales / Revenus" value={base.chargesOp + base.hypo} max={base.revNet} color="#e07070" />
            <Gauge label="Occupation seuil rentabilite" value={(base.chargesOp + base.hypo) / revBruts * 100} max={100} color="#d4a017" />
          </div>
        </div>
      )}

      {/* FRAIS */}
      {tab === "frais" && (
        <div>
          <Card title="Frais a l'achat" accent="#3d7fbf">
            <Row label="Origination fee (0.75% pret)" value={fmt(base.origination)} />
            <Row label="Appraisal + Underwriting" value={fmt(1000)} />
            <Row label="Title search + insurance" value={fmt(300 + base.titleIns)} sub="~0.4% du prix" />
            <Row label="Doc stamps hypotheque" value={fmt(base.docStampsHypo)} sub="0.35% du pret — taxe FL" />
            <Row label="Survey + Inspection" value={fmt(900)} />
            <Row label="HOA transfer + prepaid" value={fmt(1800)} />
            <Row label="Total frais d'achat" value={fmt(base.fraisAchat)} highlight color="#e07070" sub={pct(base.fraisAchatPct) + " du prix d'achat"} />
            <Row label="Cout total entree" value={fmt(base.coutEntree)} highlight color="#e07070" sub="Mise de fonds + closing costs" />
            <Row label="En CAD" value={fmt(base.coutEntree * USD_TO_CAD, "CAD")} color="#e07070" />
          </Card>
          <Card title="Frais a la vente — selon horizon" accent="#d4a017">
            <Row label="An 5 — frais de vente" value={fmt(an5.fraisVente)} sub={pct(an5.fraisVente / an5.valeur * 100) + " du prix"} color="#e07070" />
            <Row label="An 10 — frais de vente" value={fmt(an10.fraisVente)} sub={pct(an10.fraisVente / an10.valeur * 100) + " du prix"} color="#e07070" />
            <Row label={"An " + horizon + " — frais de vente"} value={fmt(sel.fraisVente)} highlight color="#e07070" />
          </Card>
          <Card title="Impact ROI selon horizon" accent="#52b788">
            <Row label="ROI an 5" value={pct(an5.roi)} color={an5.roi >= 0 ? "#52b788" : "#e07070"} />
            <Row label="ROI an 10" value={pct(an10.roi)} color={an10.roi >= 0 ? "#52b788" : "#e07070"} />
            <Row label={"ROI an " + horizon} value={pct(sel.roi)} highlight color={sel.roi >= 0 ? "#52b788" : "#e07070"} />
          </Card>
        </div>
      )}

      {/* ANNUEL */}
      {tab === "annuel" && (
        <div>
          <Card title="IRS — 1040-NR" accent="#3d7fbf">
            <Row label="Revenus annuels" value={fmt(base.revAn)} />
            <Row label="Charges operationnelles" value={"-" + fmt(base.chargesAn)} color="#e07070" />
            <Row label="Interets hypothecaires" value={"-" + fmt(base.interets)} color="#e07070" />
            <Row label="Amortissement (27.5 ans)" value={"-" + fmt(base.amortUS)} color="#e07070" sub="Deduction papier" />
            <Row label="Revenu imposable US" value={fmt(base.imposUS)} highlight color={base.imposUS <= 0 ? "#52b788" : "var(--fg-primary)"} />
            <Row label="Impot IRS (~22%)" value={fmt(base.impotUS)} color={base.impotUS < 500 ? "#52b788" : "#d4a017"} />
          </Card>
          <Card title="CRA + Revenu Quebec — T776" accent="#d4a017">
            <Row label="Revenus nets CAD" value={fmt(base.revNetCAD, "CAD")} />
            <Row label="Interets deduits CAD" value={"-" + fmt(base.interetsCAD, "CAD")} color="#52b788" info="CORRIGE" />
            <Row label="Revenu imposable" value={fmt(base.imposCAD, "CAD")} highlight />
            <Row label="Taux marginal" value={pct(base.txMarg * 100)} />
            <Row label="Impot brut" value={fmt(base.impotBrutCAD, "CAD")} />
            <Row label="Credit T2209" value={"-" + fmt(base.creditT2209, "CAD")} color="#52b788" />
            <Row label="Impot net annuel" value={fmt(base.impotCAD, "CAD")} highlight color="#d4a017" />
          </Card>
          <Card title="Formulaires obligatoires" accent="var(--fg-dim)">
            <Row label="W-8ECI" value="Obligatoire" sub="Impose sur revenu net — a faire en premier" color="#52b788" />
            <Row label="1040-NR" value="Annuel" sub="Declaration IRS non-resident" />
            <Row label="T776" value="Annuel" sub="Revenus de location CRA" />
            <Row label="T2209" value="Annuel" sub="Credit impot etranger" />
            <Row label="T1135" value="Annuel" sub="Bien etranger >100K CAD — penalite 2 500$/an" color="#e07070" />
          </Card>
          <Card title="Synthese annuelle" accent="#52b788">
            <Row label="Cash flow avant impots" value={fmt(base.cfBrut * 12)} />
            <Row label="Impots nets Canada" value={"-" + fmt(base.impotCAD / USD_TO_CAD)} color="#e07070" />
            <Row label="Cash flow net apres impots" value={fmt(base.cfNet * 12)} highlight color={cfColor} />
            <Row label="En CAD" value={fmt(base.cfNet * 12 * USD_TO_CAD, "CAD")} color={cfColor} />
            <Row label="Cash-on-Cash" value={base.coc > 0 ? pct(base.coc) : "Negatif"} highlight color={base.coc > 0 ? "#52b788" : "#e07070"} />
          </Card>
        </div>
      )}

      {/* ETF */}
      {tab === "etf" && (
        <div>
          <div style={{ background: "var(--bg-elevated)", border: "1px solid #3d7fbf33", borderRadius: 14, padding: 14, marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: "#3d7fbf", marginBottom: 6 }}>Scenario alternatif — ETF {rendementETF}%/an</div>
            <div style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.7 }}>
              Meme capital de depart ({fmt(base.coutEntree * USD_TO_CAD, "CAD")} CAD) place en ETF. Impot gain capital canadien inclus (inclusion 50% x taux marginal).
            </div>
          </div>
          <Card title="Comparaison complete" accent="var(--fg-dim)">
            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 4, marginBottom: 8 }}>
              <span />
              <span style={{ fontSize: 11, color: "#3d7fbf", textAlign: "right", fontWeight: 600 }}>ETF</span>
              <span style={{ fontSize: 11, color: "#52b788", textAlign: "right", fontWeight: 600 }}>Immo</span>
            </div>
            {[
              ["Capital depart CAD", fmt(an5.capitalETF, "CAD"), fmt(base.coutEntree * USD_TO_CAD, "CAD")],
              ["Gain net 5 ans CAD", fmt(an5.gainNetETF, "CAD"), fmt(an5.gainNet * USD_TO_CAD, "CAD")],
              ["ROI ann. 5 ans", pct(an5.roiETFAnnualise), pct(an5.roiAnnualise)],
              ["Gain net 10 ans CAD", fmt(an10.gainNetETF, "CAD"), fmt(an10.gainNet * USD_TO_CAD, "CAD")],
              ["ROI ann. 10 ans", pct(an10.roiETFAnnualise), pct(an10.roiAnnualise)],
              ["Gain net " + horizon + " ans CAD", fmt(sel.gainNetETF, "CAD"), fmt(sel.gainNet * USD_TO_CAD, "CAD")],
              ["ROI ann. " + horizon + " ans", pct(sel.roiETFAnnualise), pct(sel.roiAnnualise)],
            ].map((row) => (
              <div key={row[0]} style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", padding: "6px 0", borderBottom: "1px solid var(--divider)" }}>
                <span style={{ fontSize: 12, color: "var(--fg-muted)" }}>{row[0]}</span>
                <span style={{ fontSize: 12, color: "#3d7fbf", fontFamily: "Georgia, serif", textAlign: "right" }}>{row[1]}</span>
                <span style={{ fontSize: 12, color: "#52b788", fontFamily: "Georgia, serif", textAlign: "right" }}>{row[2]}</span>
              </div>
            ))}
          </Card>
          <Card title="Avantages non quantifies" accent="var(--fg-dim)">
            <div style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.9 }}>
              <div style={{ color: "#52b788", marginBottom: 4 }}>Avantages immobilier :</div>
              <div>Levier — appreciation sur valeur totale, pas juste ta mise</div>
              <div>Pied-a-terre garantia Orlando pour usage perso</div>
              <div>Protection inflation (actif reel)</div>
              <div>Remboursement progressif par les locataires</div>
              <div style={{ color: "#3d7fbf", marginTop: 8, marginBottom: 4 }}>Avantages ETF :</div>
              <div>Liquidite totale — vendable en 1 clic</div>
              <div>Zero gestion, zero stress operationnel</div>
              <div>Diversification automatique</div>
              <div>Pas de frais de transaction aller-retour</div>
            </div>
          </Card>
        </div>
      )}

      {tab === "canada" && (
        <div>
          <div style={{ background: "var(--bg-elevated)", border: "1px solid #d4a01733", borderRadius: 14, padding: 14, marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: "#d4a017", marginBottom: 6 }}>Strategie emprunt canadien</div>
            <div style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.7 }}>
              Emprunter via HELOC ou refinancement sur ta residence canadienne pour financer la mise de fonds ou reduire le recours au pret US non-warrantable. Les interets sont deductibles sur le T776 canadien.
            </div>
          </div>

          <Card title="Activer le scenario emprunt canadien" accent="#d4a017">
            <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 0", marginBottom: 10 }}>
              <div style={{ fontSize: 13, color: "var(--fg-muted)", flex: 1 }}>Inclure un emprunt canadien (HELOC / refi)</div>
              <button onClick={() => setCanadaActive(!canadaActive)}
                style={{ padding: "6px 18px", borderRadius: 20, border: "1px solid " + (canadaActive ? "#d4a017" : "var(--bg-chip)"), background: canadaActive ? "#d4a01722" : "transparent", color: canadaActive ? "#d4a017" : "var(--fg-faint)", cursor: "pointer", fontSize: 12 }}>
                {canadaActive ? "Actif" : "Inactif"}
              </button>
            </div>
            {canadaActive && (
              <div>
                <Slider label="Montant emprunte au Canada (CAD)" value={canadaMontant} min={10000} max={200000} step={5000} onChange={setCanadaMontant}
                  formatFn={(v) => fmt(v, "CAD")} sublabel={"Equivalent USD : " + fmt(canadaMontant / USD_TO_CAD)} />
                <Slider label="Taux HELOC / refi canadien" value={canadaTaux} min={4} max={9} step={0.25} onChange={setCanadaTaux}
                  formatFn={(v) => v + "%"} sublabel="Taux variable actuel ~6-7% (prime + 0.5%)" />
              </div>
            )}
          </Card>

          {canadaActive && (
            <div>
              <Card title="Impact sur le cash flow mensuel" accent="#e07070">
                <Row label="Cash flow sans emprunt CA" value={fmt(base.cfNetSansCanada)} color={base.cfNetSansCanada >= 0 ? "#52b788" : "#e07070"} />
                <Row label="Interets HELOC/mois (USD)" value={"-" + fmt(base.canadaInteretsMensuel / USD_TO_CAD)} color="#e07070"
                  sub={fmt(base.canadaInteretsMensuel, "CAD") + "/mois en CAD"} />
                <Row label="Cash flow avec emprunt CA" value={fmt(base.cfNet)} highlight color={base.cfNet >= 0 ? "#52b788" : "#e07070"} />
                <Row label="Difference mensuelle" value={fmt(base.cfNet - base.cfNetSansCanada)} color="#e07070" />
              </Card>

              <Card title="Avantages fiscaux — T776 Canada" accent="#52b788">
                <div style={{ background: "var(--bg-success-deep)", borderRadius: 8, padding: "9px 12px", marginBottom: 10 }}>
                  <div style={{ fontSize: 10, color: "#52b788", marginBottom: 3 }}>Deduction possible sur T776</div>
                  <div style={{ fontSize: 11, color: "var(--fg-dim)" }}>Les interets sur un emprunt canadien utilise pour investir sont deductibles au Canada — ce qui reduit ton revenu imposable QC.</div>
                </div>
                <Row label="Interets annuels HELOC (CAD)" value={fmt(base.canadaInteretsAnnuel, "CAD")} color="#52b788" info="T776" />
                <Row label="Economie impot QC estimee" value={fmt(base.canadaInteretsAnnuel * base.txMarg, "CAD")} color="#52b788"
                  sub={"Taux marginal " + pct(base.txMarg * 100) + " x interets"} />
                <Row label="Cout net apres deduction (CAD)" value={fmt(base.canadaInteretsAnnuel * (1 - base.txMarg), "CAD")} highlight color="#d4a017" />
              </Card>

              <Card title="Structure de financement complete" accent="#3d7fbf">
                <Row label="Prix d achat" value={fmt(prix)} />
                <Row label="Pret US (non-warrantable)" value={fmt(base.loan)} sub={tHypo + "% / 30 ans"} color="#e07070" />
                <Row label="Mise de fonds USD" value={fmt(base.down)} />
                <Row label="Emprunt canadien (CAD)" value={fmt(canadaMontant, "CAD")} color="#d4a017" sub={canadaTaux + "% — HELOC / refi"} />
                <Row label="Fonds propres requis (CAD)" value={fmt(Math.max(0, base.coutEntree * USD_TO_CAD - canadaMontant), "CAD")} highlight
                  sub="Cout entree - emprunt CA" />
              </Card>

              <Card title="Comparaison des deux structures" accent="var(--fg-dim)">
                <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr", gap: 4, marginBottom: 8 }}>
                  <span />
                  <span style={{ fontSize: 11, color: "#3d7fbf", textAlign: "right", fontWeight: 600 }}>Sans CA</span>
                  <span style={{ fontSize: 11, color: "#d4a017", textAlign: "right", fontWeight: 600 }}>Avec CA</span>
                </div>
                {[
                  ["Fonds propres CAD", fmt(base.coutEntree * USD_TO_CAD, "CAD"), fmt(Math.max(0, base.coutEntree * USD_TO_CAD - canadaMontant), "CAD")],
                  ["CF mensuel", fmt(base.cfNetSansCanada), fmt(base.cfNet)],
                  ["Interets tot./mois", fmt(base.hypo), fmt(base.hypo + base.canadaInteretsMensuel / USD_TO_CAD)],
                  ["Deduction T776 CA/an", fmt(base.interetsCAD, "CAD"), fmt(base.interetsCAD + base.canadaInteretsAnnuel, "CAD")],
                  ["Impot QC economise", fmt(base.creditT2209, "CAD"), fmt(base.creditT2209 + base.canadaInteretsAnnuel * base.txMarg, "CAD")],
                ].map((row) => (
                  <div key={row[0]} style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr", padding: "6px 0", borderBottom: "1px solid var(--divider)" }}>
                    <span style={{ fontSize: 12, color: "var(--fg-muted)" }}>{row[0]}</span>
                    <span style={{ fontSize: 12, color: "#3d7fbf", fontFamily: "Georgia, serif", textAlign: "right" }}>{row[1]}</span>
                    <span style={{ fontSize: 12, color: "#d4a017", fontFamily: "Georgia, serif", textAlign: "right" }}>{row[2]}</span>
                  </div>
                ))}
              </Card>

              <Card title="Points d attention" accent="#e07070">
                <div style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.9 }}>
                  <div>Ta residence canadienne est donnee en garantie — risque si difficultes</div>
                  <div>Taux HELOC variable — si prime monte, ton cash flow se degrade</div>
                  <div>La CRA exige une traçabilite claire de l utilisation des fonds</div>
                  <div>Consulte un CPA transfrontalier pour valider la deductibilite</div>
                </div>
              </Card>
            </div>
          )}
        </div>
      )}

      <div style={{ textAlign: "center", fontSize: 10, color: "var(--axis)", marginTop: 8, lineHeight: 1.7 }}>
        Modèle indicatif · 1 USD = 1.38 CAD · T776 intérêts déduits · Frais FL 2026
        <br />Consulte un CPA transfrontalier CA/US
        {meta && (
          <div style={{ marginTop: 6, fontSize: 9, color: "var(--border)" }}>
            Données générées {new Date(meta.generated_at).toLocaleString("fr-CA")} · {properties.length} propriétés
            {meta.local_mode ? " · mode LOCAL" : " · " + (meta.total_api_calls || 0) + " appels API"}
            {" · navigation clavier : ◀ ▶ ou 1–9"}
          </div>
        )}
      </div>
    </div>
  );
}
