/* ==========================================================================
   OWOO Eco-Acoustic LoRaWAN Command Center — Frontend Application Logic
   ========================================================================== */

const KATINGAN_CENTER = [-2.625, 113.135];
const GATEWAY_COORD = [-2.595, 113.142];

// Approximate conservation corridor polygon of Katingan Mentaya Project (Central Kalimantan)
const KATINGAN_BOUNDARY = [
  [-2.18, 113.06],
  [-2.24, 113.26],
  [-2.48, 113.31],
  [-2.76, 113.27],
  [-2.98, 113.16],
  [-3.02, 112.98],
  [-2.78, 112.95],
  [-2.44, 112.99],
];

const appState = {
  activeTab: 'overview',
  nodes: [],
  timeseries: [],
  summaries: [],
  logs: [],
  serverLogs: [],
  mqtt: {},
  selectedNodeId: null,
  pickingCoordinateOnMap: false,
  revealedKeyNodeId: null,
};

let overviewMap = null;
let overviewLayerGroup = null;
let katinganMap = null;
let katinganLayerGroup = null;
let tempPickMarker = null;

let aciOverviewChart = null;
let ndsiOverviewChart = null;
let nodeDetailChart = null;
let reportTrendChart = null;
let reportClassChart = null;

let reportSearchTimer = null;

/* ---------------- UTILITIES ---------------- */

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatNum(val, digits = 2, forceSign = false) {
  if (val === null || val === undefined || isNaN(Number(val))) return '--';
  const n = Number(val);
  const str = n.toFixed(digits);
  return forceSign && n > 0 ? `+${str}` : str;
}

function formatShortTime(ts) {
  if (!ts) return '--:--';
  const parts = String(ts).split(' ');
  if (parts.length > 1) return parts[1].slice(0, 5);
  return String(ts).slice(11, 16) || ts;
}

function conditionTheme(condition) {
  const c = (condition || '').toLowerCase();
  if (c === 'threat') {
    return {
      key: 'threat',
      color: '#FF2A4B',
      fill: 'rgba(255, 42, 75, 0.24)',
      badgeClass: 'badge-threat',
      title: 'Chainsaw (Threat)',
      shortLabel: 'Threat',
    };
  }
  if (c === 'biodiversity') {
    return {
      key: 'biodiversity',
      color: '#00FF88',
      fill: 'rgba(0, 255, 136, 0.17)',
      badgeClass: 'badge-biodiversity',
      title: 'Ambient Hutan (Biodiversity)',
      shortLabel: 'Biodiversity',
    };
  }
  if (c === 'noise') {
    return {
      key: 'noise',
      color: '#38BDF8',
      fill: 'rgba(56, 189, 248, 0.17)',
      badgeClass: 'badge-noise',
      title: 'Rain/Wind (Noise)',
      shortLabel: 'Noise',
    };
  }
  return {
    key: 'waiting',
    color: '#94A3B8',
    fill: 'rgba(148, 163, 184, 0.14)',
    badgeClass: 'badge-waiting',
    title: 'Menunggu Data Uplink',
    shortLabel: 'Standby',
  };
}

function renderConditionBadge(classification, condition) {
  const th = conditionTheme(condition);
  const label = classification ? `${escapeHtml(classification)} • ${th.shortLabel}` : th.title;
  return `<span class="badge ${th.badgeClass}"><span class="badge-dot"></span>${label}</span>`;
}

function showToast(message, isThreat = false) {
  const stack = document.getElementById('toastStack');
  if (!stack) return;
  const item = document.createElement('div');
  item.className = 'toast-item' + (isThreat ? ' toast-threat' : '');
  item.innerHTML = `
    <span class="badge-dot" style="background:${isThreat ? '#FF2A4B' : '#00FF88'}; width:9px; height:9px; border-radius:50%; flex-shrink:0;"></span>
    <span>${escapeHtml(message)}</span>
  `;
  stack.appendChild(item);
  setTimeout(() => {
    item.style.opacity = '0';
    item.style.transition = 'opacity 0.3s ease';
    setTimeout(() => item.remove(), 320);
  }, 4200);
}

/* ---------------- LIVE CLOCK ---------------- */

function startClock() {
  const el = document.getElementById('liveClock');
  const tick = () => {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    if (el) el.textContent = `${hh}:${mm}:${ss} WIB`;
  };
  tick();
  setInterval(tick, 1000);
}

/* ---------------- NAVIGATION TABS ---------------- */

function switchMainTab(tabName) {
  appState.activeTab = tabName;
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-pane').forEach((pane) => {
    pane.classList.toggle('active', pane.id === `tab-${tabName}`);
  });

  const titles = {
    overview: 'Acoustic Overview Dashboard',
    map: 'Katingan Mentaya Project — End Node Map',
    reports: 'Acoustic Reports & Local Server Logs',
  };
  const titleEl = document.getElementById('topbarTabTitle');
  if (titleEl) titleEl.textContent = titles[tabName] || 'OWOO Monitoring';

  setTimeout(() => {
    if (tabName === 'overview' && overviewMap) {
      overviewMap.invalidateSize();
    } else if (tabName === 'map' && katinganMap) {
      katinganMap.invalidateSize();
      if (!appState.selectedNodeId && appState.nodes.length > 0) {
        selectNodeOnMap(appState.nodes[0].id, false);
      }
    } else if (tabName === 'reports') {
      loadReportsData();
    }
  }, 80);
}

/* ---------------- GEOMETRY FOR BIO-ACOUSTIC OCTAGON ZONES ---------------- */

function createOctagonCoords(lat, lng, radiusDeg = 0.042) {
  const pts = [];
  for (let i = 0; i < 8; i++) {
    const angle = (Math.PI / 4) * i + Math.PI / 8;
    const dLat = radiusDeg * 0.82 * Math.sin(angle);
    const dLng = radiusDeg * 1.12 * Math.cos(angle);
    pts.push([lat + dLat, lng + dLng]);
  }
  return pts;
}

function createNodeMarkerIcon(node) {
  const th = conditionTheme(node.last_condition);
  let innerSvg = '';
  if (th.key === 'threat') {
    innerSvg = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;
  } else if (th.key === 'noise') {
    innerSvg = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2.3"><path d="M20 17.58A5 5 0 0 0 18 8h-1.26A8 8 0 1 0 4 16.25"/><line x1="8" y1="16" x2="8.01" y2="20"/><line x1="12" y1="18" x2="12.01" y2="22"/><line x1="16" y1="16" x2="16.01" y2="20"/></svg>`;
  } else {
    innerSvg = `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2.3"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/></svg>`;
  }

  return L.divIcon({
    className: 'owoo-custom-div-icon',
    html: `<div class="owoo-marker-pin ${th.key}">${innerSvg}</div>`,
    iconSize: [38, 38],
    iconAnchor: [19, 19],
    popupAnchor: [0, -18],
  });
}

function createGatewayIcon() {
  return L.divIcon({
    className: 'owoo-gateway-icon',
    html: `
      <div style="width:34px;height:34px;border-radius:10px;background:rgba(5,22,16,0.94);border:2px solid #00FF88;box-shadow:0 0 16px rgba(0,255,136,0.55);display:flex;align-items:center;justify-content:center;color:#00FF88;">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2.2">
          <circle cx="12" cy="12" r="2.5"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4m-8.4 0a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2m-14.2 0a10 10 0 0 1 0-14.2"/>
        </svg>
      </div>
    `,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });
}

/* ---------------- LEAFLET MAPS INIT & RENDERING ---------------- */

function initMaps() {
  overviewMap = L.map('overviewMap', {
    center: KATINGAN_CENTER,
    zoom: 9,
    zoomControl: false,
    attributionControl: false,
  });
  L.control.zoom({ position: 'topright' }).addTo(overviewMap);

  L.tileLayer('/api/tiles/{z}/{x}/{y}.jpg', {
    maxZoom: 16,
    minZoom: 7,
  }).addTo(overviewMap);

  overviewLayerGroup = L.layerGroup().addTo(overviewMap);

  katinganMap = L.map('katinganMap', {
    center: KATINGAN_CENTER,
    zoom: 10,
    zoomControl: true,
    attributionControl: false,
  });

  L.tileLayer('/api/tiles/{z}/{x}/{y}.jpg', {
    maxZoom: 16,
    minZoom: 7,
  }).addTo(katinganMap);

  katinganLayerGroup = L.layerGroup().addTo(katinganMap);

  // Map click handler for coordinate picker mode
  katinganMap.on('click', (ev) => {
    if (!appState.pickingCoordinateOnMap) return;
    const lat = ev.latlng.lat.toFixed(5);
    const lng = ev.latlng.lng.toFixed(5);
    document.getElementById('nodeLatInput').value = lat;
    document.getElementById('nodeLngInput').value = lng;

    if (tempPickMarker) katinganMap.removeLayer(tempPickMarker);
    tempPickMarker = L.circleMarker([lat, lng], {
      radius: 9,
      color: '#00FF88',
      fillColor: '#00FF88',
      fillOpacity: 0.8,
    }).addTo(katinganMap);

    cancelMapCoordinatePick(true);
    showToast(`Koordinat dipilih: (${lat}, ${lng})`);
  });
}

function renderOverviewMapNodes() {
  if (!overviewMap || !overviewLayerGroup) return;
  overviewLayerGroup.clearLayers();

  // Conservation boundary outline
  L.polygon(KATINGAN_BOUNDARY, {
    color: 'rgba(0, 255, 136, 0.32)',
    weight: 1.5,
    dashArray: '6, 6',
    fillColor: '#00FF88',
    fillOpacity: 0.03,
  }).addTo(overviewLayerGroup);

  // Central LoRaWAN WisGate + Raspberry Pi Gateway
  L.marker(GATEWAY_COORD, { icon: createGatewayIcon() })
    .bindPopup(
      `<div style="min-width:190px;">
        <div style="font-weight:800;color:#00FF88;margin-bottom:3px;">OWOO LoRaWAN Gateway</div>
        <div style="font-size:11.5px;color:#9BBDB0;">WisGate 923 MHz + Raspberry Pi ChirpStack Server (Katingan Mentaya)</div>
      </div>`
    )
    .addTo(overviewLayerGroup);

  appState.nodes.forEach((node) => {
    const th = conditionTheme(node.last_condition);

    // Dashed LoRaWAN RF telemetry link line
    L.polyline([GATEWAY_COORD, [node.latitude, node.longitude]], {
      color: th.color,
      weight: 1.6,
      opacity: 0.55,
      dashArray: '5, 7',
    }).addTo(overviewLayerGroup);

    // Glowing Octagonal Geofence Zone (like Reference Screenshot!)
    const octagon = L.polygon(createOctagonCoords(node.latitude, node.longitude, 0.048), {
      color: th.color,
      weight: 2.2,
      fillColor: th.color,
      fillOpacity: th.key === 'threat' ? 0.24 : 0.14,
    }).addTo(overviewLayerGroup);

    const popupHtml = buildMapPopupHtml(node, true);
    octagon.bindPopup(popupHtml);

    const marker = L.marker([node.latitude, node.longitude], {
      icon: createNodeMarkerIcon(node),
    }).addTo(overviewLayerGroup);
    marker.bindPopup(popupHtml);
  });
}

function renderMapNodes() {
  if (!katinganMap || !katinganLayerGroup) return;
  katinganLayerGroup.clearLayers();

  const condFilter = document.getElementById('mapConditionFilter')?.value || 'all';

  // Katingan Mentaya Project Concession Boundary
  L.polygon(KATINGAN_BOUNDARY, {
    color: 'rgba(0, 255, 136, 0.42)',
    weight: 2,
    dashArray: '8, 6',
    fillColor: '#00FF88',
    fillOpacity: 0.035,
  })
    .bindTooltip('Kawasan Katingan Mentaya Project', { sticky: true })
    .addTo(katinganLayerGroup);

  // Central Gateway
  L.marker(GATEWAY_COORD, { icon: createGatewayIcon() })
    .bindPopup(
      `<div style="min-width:210px;">
        <div style="font-weight:800;color:#00FF88;margin-bottom:4px;">WisGate + Raspberry Pi LNS</div>
        <div style="font-size:11.5px;color:#9BBDB0;">Pusat Penerima Private LoRaWAN 923 MHz &amp; Local ChirpStack MQTT Server</div>
      </div>`
    )
    .addTo(katinganLayerGroup);

  const visibleNodes = appState.nodes.filter((n) => {
    if (condFilter === 'all') return true;
    return (n.last_condition || '').toLowerCase() === condFilter.toLowerCase();
  });

  visibleNodes.forEach((node) => {
    const th = conditionTheme(node.last_condition);
    const isSelected = node.id === appState.selectedNodeId;

    L.polyline([GATEWAY_COORD, [node.latitude, node.longitude]], {
      color: th.color,
      weight: isSelected ? 2.5 : 1.5,
      opacity: isSelected ? 0.85 : 0.45,
      dashArray: '5, 7',
    }).addTo(katinganLayerGroup);

    const zone = L.polygon(createOctagonCoords(node.latitude, node.longitude, 0.044), {
      color: th.color,
      weight: isSelected ? 3 : 2,
      fillColor: th.color,
      fillOpacity: th.key === 'threat' ? 0.25 : 0.14,
    }).addTo(katinganLayerGroup);

    zone.on('click', () => selectNodeOnMap(node.id, true));

    const marker = L.marker([node.latitude, node.longitude], {
      icon: createNodeMarkerIcon(node),
    }).addTo(katinganLayerGroup);

    marker.bindPopup(buildMapPopupHtml(node, false));
    marker.on('click', () => selectNodeOnMap(node.id, false));
  });

  renderMapSidebarList(visibleNodes);
}

function buildMapPopupHtml(node, isOverview) {
  const th = conditionTheme(node.last_condition);
  return `
    <div style="min-width: 235px; padding: 3px;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:5px;">
        <strong style="font-size:13.5px;color:#ECFDF5;">${escapeHtml(node.name)}</strong>
        <span style="font-size:10.5px;font-weight:800;color:${th.color};">${th.shortLabel.toUpperCase()}</span>
      </div>
      <div style="font-size:11.5px;color:#9BBDB0;margin-bottom:8px;">${escapeHtml(node.location_name)}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;">
        <div style="background:rgba(0,255,136,0.08);padding:6px 8px;border-radius:8px;">
          <div style="font-size:10px;color:#9BBDB0;">ACI</div>
          <div class="mono" style="font-size:14px;font-weight:800;color:#00FF88;">${formatNum(node.last_aci, 2)}</div>
        </div>
        <div style="background:rgba(56,189,248,0.08);padding:6px 8px;border-radius:8px;">
          <div style="font-size:10px;color:#9BBDB0;">NDSI</div>
          <div class="mono" style="font-size:14px;font-weight:800;color:#7DD3FC;">${formatNum(node.last_ndsi, 2, true)}</div>
        </div>
      </div>
      <div style="font-size:11.5px;margin-bottom:8px;">
        Klasifikasi: <strong style="color:${th.color};">${escapeHtml(node.last_classification || 'Menunggu Uplink')}</strong>
      </div>
      ${
        isOverview
          ? `<button type="button" class="btn btn-sm btn-neon" style="width:100%;" onclick="inspectNodeFromOverview(${node.id})">Lihat Detail Node &rarr;</button>`
          : `<div class="mono" style="font-size:10px;color:#5E8777;">DevEUI: ${escapeHtml(node.dev_eui)}</div>`
      }
    </div>
  `;
}

function inspectNodeFromOverview(nodeId) {
  switchMainTab('map');
  setTimeout(() => selectNodeOnMap(nodeId, true), 120);
}

function recenterOverviewMap() {
  if (overviewMap) overviewMap.flyTo(KATINGAN_CENTER, 9, { duration: 0.7 });
}

function recenterKatinganMap() {
  if (katinganMap) katinganMap.flyTo(KATINGAN_CENTER, 10, { duration: 0.7 });
}

/* ---------------- SELECTED END NODE INSPECTOR (MAP TAB) ---------------- */

function renderMapSidebarList(nodesToShow) {
  const countEl = document.getElementById('mapNodeCount');
  if (countEl) countEl.textContent = nodesToShow.length;

  const listEl = document.getElementById('mapNodeList');
  if (!listEl) return;

  if (nodesToShow.length === 0) {
    listEl.innerHTML = `<div style="padding:12px;color:var(--text-muted);font-size:12px;text-align:center;">Tidak ada End Node pada filter ini.</div>`;
    return;
  }

  listEl.innerHTML = nodesToShow
    .map((n) => {
      const th = conditionTheme(n.last_condition);
      const selectedCls = n.id === appState.selectedNodeId ? 'selected' : '';
      return `
        <div class="node-list-item ${selectedCls}" onclick="selectNodeOnMap(${n.id}, true)">
          <div style="min-width:0;">
            <div style="font-weight:700;font-size:12.8px;color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              ${escapeHtml(n.name)}
            </div>
            <div style="font-size:11px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              ${escapeHtml(n.location_name)}
            </div>
          </div>
          <span class="badge ${th.badgeClass}" style="font-size:10.5px;padding:3px 8px;">
            <span class="badge-dot"></span>${th.shortLabel}
          </span>
        </div>
      `;
    })
    .join('');
}

async function selectNodeOnMap(nodeId, fly = true) {
  appState.selectedNodeId = nodeId;
  renderMapNodes();

  const node = appState.nodes.find((n) => n.id === nodeId);
  if (node && fly && katinganMap) {
    katinganMap.flyTo([node.latitude, node.longitude], 11, { duration: 0.65 });
  }

  try {
    const res = await fetch(`/api/nodes/${nodeId}`);
    const data = await res.json();
    if (!data.ok) return;
    renderNodeInspectorDetail(data.node, data.history || []);
  } catch (err) {
    console.error('Failed loading node detail:', err);
  }
}

function renderNodeInspectorDetail(node, history) {
  const container = document.getElementById('nodeDetailContainer');
  if (!container || !node) return;

  const cond = (node.last_condition || '').toLowerCase();
  const keyVisible = appState.revealedKeyNodeId === node.id;
  const maskedKey = keyVisible
    ? node.node_key
    : `${(node.node_key || '').slice(0, 6)}••••••••••••••••${(node.node_key || '').slice(-4)}`;

  container.innerHTML = `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;">
      <div>
        <h3 style="font-size:16px;font-weight:800;color:var(--text-primary);">${escapeHtml(node.name)}</h3>
        <div style="font-size:12px;color:var(--text-secondary);">${escapeHtml(node.location_name)}</div>
        <div class="mono" style="font-size:11px;color:var(--text-muted);margin-top:2px;">
          Lat ${Number(node.latitude).toFixed(4)}, Lng ${Number(node.longitude).toFixed(4)}
        </div>
      </div>
      ${renderConditionBadge(node.last_classification, node.last_condition)}
    </div>

    <!-- ACI & NDSI Live Readouts -->
    <div class="metric-pair">
      <div class="metric-mini-card">
        <div style="font-size:11px;color:var(--text-secondary);font-weight:600;">Indeks ACI</div>
        <div class="mono" style="font-size:22px;font-weight:800;color:var(--neon-green);margin-top:2px;">
          ${formatNum(node.last_aci, 3)}
        </div>
        <div style="font-size:10.5px;color:var(--text-muted);">Acoustic Complexity</div>
      </div>
      <div class="metric-mini-card">
        <div style="font-size:11px;color:var(--text-secondary);font-weight:600;">Indeks NDSI</div>
        <div class="mono" style="font-size:22px;font-weight:800;color:#7DD3FC;margin-top:2px;">
          ${formatNum(node.last_ndsi, 3, true)}
        </div>
        <div style="font-size:10.5px;color:var(--text-muted);">Soundscape Ratio</div>
      </div>
    </div>

    <!-- 3 Kondisi Klasifikasi Audio -->
    <div>
      <div style="font-size:11.5px;font-weight:700;color:var(--text-secondary);margin-bottom:7px;text-transform:uppercase;letter-spacing:0.05em;">
        Status Klasifikasi Audio (3 Kondisi)
      </div>
      <div class="conditions-grid">
        <div class="condition-box ${cond === 'threat' ? 'active-threat' : ''}">
          <div>
            <div style="font-weight:800;font-size:12.5px;color:#FF6B81;">1. Chainsaw</div>
            <div style="font-size:11px;color:var(--text-secondary);">Kondisi: <strong>Threat</strong> (Ancaman Pembalakan)</div>
          </div>
          <span class="badge badge-threat">${cond === 'threat' ? 'AKTIF TERDETEKSI' : 'Aman'}</span>
        </div>

        <div class="condition-box ${cond === 'biodiversity' ? 'active-biodiversity' : ''}">
          <div>
            <div style="font-weight:800;font-size:12.5px;color:var(--neon-green);">2. Ambient Hutan</div>
            <div style="font-size:11px;color:var(--text-secondary);">Kondisi: <strong>Biodiversity</strong> (Ekosistem Satwa)</div>
          </div>
          <span class="badge badge-biodiversity">${cond === 'biodiversity' ? 'AKTIF TERDETEKSI' : 'Standby'}</span>
        </div>

        <div class="condition-box ${cond === 'noise' ? 'active-noise' : ''}">
          <div>
            <div style="font-weight:800;font-size:12.5px;color:var(--noise-cyan);">3. Rain/Wind</div>
            <div style="font-size:11px;color:var(--text-secondary);">Kondisi: <strong>Noise</strong> (Hujan / Angin Geophony)</div>
          </div>
          <span class="badge badge-noise">${cond === 'noise' ? 'AKTIF TERDETEKSI' : 'Standby'}</span>
        </div>
      </div>
    </div>

    <!-- Mini Historical Chart for This End Node -->
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-size:11.5px;font-weight:700;color:var(--text-secondary);">Riwayat ACI &amp; NDSI Node Ini</span>
        <span class="mono" style="font-size:10.5px;color:var(--text-muted);">${escapeHtml(node.last_seen || 'Belum ada uplink')}</span>
      </div>
      <div style="height:135px;position:relative;">
        <canvas id="nodeDetailChart"></canvas>
      </div>
    </div>

    <!-- ChirpStack Topic & Unique Key Metadata -->
    <div style="display:flex;flex-direction:column;gap:6px;">
      <div style="display:flex;justify-content:space-between;font-size:11.5px;">
        <span style="color:var(--text-secondary);">App ID / DevEUI:</span>
        <span class="mono" style="color:var(--text-primary);">${escapeHtml(node.app_id)} / ${escapeHtml(node.dev_eui)}</span>
      </div>
      <div style="font-size:11px;color:var(--text-secondary);">Topic Subscribe MQTT:</div>
      <div class="topic-code-box">${escapeHtml(node.mqtt_topic)}</div>

      <div style="display:flex;align-items:center;justify-content:space-between;margin-top:2px;">
        <span style="font-size:11px;color:var(--text-secondary);">Node Key Unik:</span>
        <button type="button" style="background:none;border:none;color:var(--neon-green);font-size:11px;cursor:pointer;font-weight:700;" onclick="toggleNodeKeyVisibility(${node.id})">
          ${keyVisible ? 'Sembunyikan' : 'Tampilkan Key'}
        </button>
      </div>
      <div class="topic-code-box" style="color:#CBD5E1;">${escapeHtml(maskedKey)}</div>
    </div>

    <!-- Quick Test Uplink & Delete Node Buttons -->
    <div style="display:flex;flex-direction:column;gap:8px;padding-top:4px;">
      <div style="font-size:11px;font-weight:700;color:var(--text-secondary);">Uji Kirim Paket MQTT ke Node Ini:</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;">
        <button type="button" class="btn btn-sm btn-ghost" onclick="simulateNodePacket(${node.id}, 'biodiversity')">Ambient</button>
        <button type="button" class="btn btn-sm btn-crimson" onclick="simulateNodePacket(${node.id}, 'threat')">Chainsaw</button>
        <button type="button" class="btn btn-sm btn-cyan" onclick="simulateNodePacket(${node.id}, 'noise')">Rain/Wind</button>
      </div>
      <button type="button" class="btn btn-sm btn-crimson" style="width:100%;margin-top:4px;" onclick="confirmDeleteNode(${node.id}, '${escapeHtml(node.name)}')">
        Hapus End Node dari Sistem
      </button>
    </div>
  `;

  renderNodeDetailMiniChart(history);
}

function toggleNodeKeyVisibility(nodeId) {
  appState.revealedKeyNodeId = appState.revealedKeyNodeId === nodeId ? null : nodeId;
  selectNodeOnMap(nodeId, false);
}

function renderNodeDetailMiniChart(history) {
  const ctx = document.getElementById('nodeDetailChart');
  if (!ctx) return;
  if (nodeDetailChart) nodeDetailChart.destroy();

  const labels = history.map((h) => formatShortTime(h.recorded_at));
  const aciData = history.map((h) => h.aci);
  const ndsiData = history.map((h) => h.ndsi);

  nodeDetailChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'ACI',
          data: aciData,
          borderColor: '#00FF88',
          backgroundColor: 'rgba(0, 255, 136, 0.14)',
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.36,
          fill: true,
        },
        {
          label: 'NDSI',
          data: ndsiData,
          borderColor: '#38BDF8',
          backgroundColor: 'rgba(56, 189, 248, 0.08)',
          borderWidth: 1.8,
          pointRadius: 2,
          tension: 0.36,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: '#9BBDB0', boxWidth: 10, font: { size: 10 } },
        },
      },
      scales: {
        x: {
          ticks: { color: '#5E8777', font: { size: 9.5 }, maxTicksLimit: 6 },
          grid: { color: 'rgba(0,255,136,0.05)' },
        },
        y: {
          ticks: { color: '#5E8777', font: { size: 9.5 }, maxTicksLimit: 5 },
          grid: { color: 'rgba(0,255,136,0.06)' },
        },
      },
    },
  });
}

/* ---------------- OVERVIEW CHARTS (ACI & NDSI) ---------------- */

function initOverviewCharts() {
  const aciCanvas = document.getElementById('aciOverviewChart');
  const ndsiCanvas = document.getElementById('ndsiOverviewChart');
  if (!aciCanvas || !ndsiCanvas) return;

  const aciCtx = aciCanvas.getContext('2d');
  const aciGrad = aciCtx.createLinearGradient(0, 0, 0, 220);
  aciGrad.addColorStop(0, 'rgba(0, 255, 136, 0.38)');
  aciGrad.addColorStop(1, 'rgba(0, 255, 136, 0.01)');

  aciOverviewChart = new Chart(aciCtx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'ACI (Acoustic Complexity Index)',
          data: [],
          borderColor: '#00FF88',
          backgroundColor: aciGrad,
          borderWidth: 2.6,
          pointBackgroundColor: '#00FF88',
          pointBorderColor: '#051A12',
          pointRadius: 3,
          pointHoverRadius: 5,
          tension: 0.38,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(5, 20, 14, 0.94)',
          borderColor: 'rgba(0, 255, 136, 0.4)',
          borderWidth: 1,
          titleColor: '#ECFDF5',
          bodyColor: '#00FF88',
        },
      },
      scales: {
        x: {
          ticks: { color: '#6E9988', font: { size: 10.5 }, maxTicksLimit: 8 },
          grid: { color: 'rgba(0, 255, 136, 0.06)' },
        },
        y: {
          ticks: { color: '#6E9988', font: { size: 10.5 } },
          grid: { color: 'rgba(0, 255, 136, 0.08)' },
        },
      },
    },
  });

  const ndsiCtx = ndsiCanvas.getContext('2d');
  const ndsiGradGreen = ndsiCtx.createLinearGradient(0, 0, 0, 220);
  ndsiGradGreen.addColorStop(0, 'rgba(0, 255, 136, 0.28)');
  ndsiGradGreen.addColorStop(1, 'rgba(0, 255, 136, 0.0)');

  const ndsiGradCyan = ndsiCtx.createLinearGradient(0, 0, 0, 220);
  ndsiGradCyan.addColorStop(0, 'rgba(56, 189, 248, 0.28)');
  ndsiGradCyan.addColorStop(1, 'rgba(99, 102, 241, 0.03)');

  ndsiOverviewChart = new Chart(ndsiCtx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'NDSI Score (-1 s/d +1)',
          data: [],
          borderColor: '#00FF88',
          backgroundColor: ndsiGradGreen,
          borderWidth: 2.5,
          pointRadius: 2.8,
          tension: 0.38,
          fill: true,
        },
        {
          label: 'Anthrophony Disturbance',
          data: [],
          borderColor: '#38BDF8',
          backgroundColor: ndsiGradCyan,
          borderWidth: 1.8,
          pointRadius: 0,
          tension: 0.42,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: '#9BBDB0', boxWidth: 11, font: { size: 11 } },
        },
        tooltip: {
          backgroundColor: 'rgba(5, 20, 14, 0.94)',
          borderColor: 'rgba(56, 189, 248, 0.4)',
          borderWidth: 1,
        },
      },
      scales: {
        x: {
          ticks: { color: '#6E9988', font: { size: 10.5 }, maxTicksLimit: 8 },
          grid: { color: 'rgba(0, 255, 136, 0.06)' },
        },
        y: {
          min: -1.0,
          max: 1.0,
          ticks: { color: '#6E9988', font: { size: 10.5 } },
          grid: { color: 'rgba(0, 255, 136, 0.08)' },
        },
      },
    },
  });
}

function updateOverviewCharts(series) {
  if (!aciOverviewChart || !ndsiOverviewChart) return;

  const labels = series.map((item) => `${formatShortTime(item.recorded_at)} (${item.node_name.replace('SylvaSense ', '')})`);
  const aciVals = series.map((item) => item.aci);
  const ndsiVals = series.map((item) => item.ndsi);
  // Complementary anthrophonic disturbance wave (derived from NDSI = (B-A)/(B+A))
  const disturbanceVals = series.map((item) => Number(((1 - item.ndsi) / 2 - 0.5).toFixed(3)));

  aciOverviewChart.data.labels = labels;
  aciOverviewChart.data.datasets[0].data = aciVals;
  aciOverviewChart.update('none');

  ndsiOverviewChart.data.labels = labels;
  ndsiOverviewChart.data.datasets[0].data = ndsiVals;
  ndsiOverviewChart.data.datasets[1].data = disturbanceVals;
  ndsiOverviewChart.update('none');

  if (series.length > 0) {
    const latest = series[series.length - 1];
    document.getElementById('overviewLatestAci').textContent = formatNum(latest.aci, 2);
    const ndsiEl = document.getElementById('overviewLatestNdsi');
    const ndsiLabelEl = document.getElementById('overviewNdsiLabel');
    ndsiEl.textContent = formatNum(latest.ndsi, 2, true);
    if (latest.ndsi < -0.15) {
      ndsiEl.style.color = '#FF4D6A';
      ndsiLabelEl.textContent = 'Gangguan Anthrophony!';
    } else if (latest.ndsi > 0.35) {
      ndsiEl.style.color = '#00FF88';
      ndsiLabelEl.textContent = 'Biophony Sehat';
    } else {
      ndsiEl.style.color = '#38BDF8';
      ndsiLabelEl.textContent = 'Geophony / Netral';
    }
  }
}

async function handleOverviewFilterChange() {
  const nodeId = document.getElementById('overviewNodeFilter').value;
  const url = nodeId ? `/api/timeseries?node_id=${nodeId}&limit=36` : `/api/timeseries?limit=48`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (data.ok) {
      appState.timeseries = data.timeseries;
      updateOverviewCharts(data.timeseries);
    }
  } catch (err) {
    console.error(err);
  }
}

/* ---------------- KPI & THREAT BANNER UPDATES ---------------- */

function refreshKpisAndSelectors() {
  const nodes = appState.nodes;
  document.getElementById('kpiTotalNodes').textContent = nodes.length;

  const activeDataNodes = nodes.filter((n) => n.last_aci !== null && n.last_aci !== undefined);
  const avgAci =
    activeDataNodes.length > 0
      ? activeDataNodes.reduce((acc, n) => acc + Number(n.last_aci), 0) / activeDataNodes.length
      : 0;
  const avgNdsi =
    activeDataNodes.length > 0
      ? activeDataNodes.reduce((acc, n) => acc + Number(n.last_ndsi), 0) / activeDataNodes.length
      : 0;

  document.getElementById('kpiAvgAci').textContent = formatNum(avgAci, 2);
  document.getElementById('kpiAvgNdsi').textContent = formatNum(avgNdsi, 2, true);
  document.getElementById('kpiNdsiStatus').textContent =
    avgNdsi >= 0.35 ? 'Ekosistem Biophony Sehat' : avgNdsi < 0 ? 'Terindikasi Tekanan Akustik' : 'Transisi Geophony';

  const threatNodes = nodes.filter((n) => (n.last_condition || '').toLowerCase() === 'threat');
  document.getElementById('kpiThreatNodes').textContent = threatNodes.length;
  const threatCard = document.getElementById('kpiThreatCard');
  const navBadge = document.getElementById('navThreatBadge');
  const banner = document.getElementById('globalThreatBanner');

  if (threatNodes.length > 0) {
    threatCard.classList.add('alert-border');
    document.getElementById('kpiThreatSub').textContent = `${threatNodes.length} Node mendeteksi Chainsaw!`;
    navBadge.style.display = 'inline-block';
    navBadge.textContent = threatNodes.length;

    const firstThreat = threatNodes[0];
    banner.classList.add('visible');
    document.getElementById('globalThreatText').textContent =
      `Deteksi Chainsaw (Threat) aktif pada ${firstThreat.name} — ${firstThreat.location_name} (ACI ${formatNum(firstThreat.last_aci, 2)}, NDSI ${formatNum(firstThreat.last_ndsi, 2, true)}).`;
    document.getElementById('jumpThreatNodeBtn').onclick = () => inspectNodeFromOverview(firstThreat.id);
  } else {
    threatCard.classList.remove('alert-border');
    document.getElementById('kpiThreatSub').textContent = 'Seluruh Zona Katingan Mentaya Aman';
    navBadge.style.display = 'none';
    banner.classList.remove('visible');
  }

  // Populate Node Dropdowns across Overview, Reports, and Simulator
  const overviewSel = document.getElementById('overviewNodeFilter');
  const repSel = document.getElementById('repNodeFilter');
  const simSel = document.getElementById('simTargetNodeSelect');

  const prevOverview = overviewSel ? overviewSel.value : '';
  const prevRep = repSel ? repSel.value : '';
  const prevSim = simSel ? simSel.value : '';

  const nodeOptions = nodes
    .map((n) => `<option value="${n.id}">${escapeHtml(n.name)} — ${escapeHtml(n.location_name)}</option>`)
    .join('');

  if (overviewSel) {
    overviewSel.innerHTML = `<option value="">Semua End Node</option>` + nodeOptions;
    overviewSel.value = prevOverview;
  }
  if (repSel) {
    repSel.innerHTML = `<option value="">Semua End Node &amp; Lokasi</option>` + nodeOptions;
    repSel.value = prevRep;
  }
  if (simSel) {
    simSel.innerHTML = nodeOptions;
    if (prevSim) simSel.value = prevSim;
  }
}

function updateMqttStatusUi(mqtt) {
  if (!mqtt) return;
  appState.mqtt = mqtt;
  const dot = document.getElementById('sidebarMqttDot');
  const stateEl = document.getElementById('sidebarMqttState');
  const brokerEl = document.getElementById('sidebarMqttBroker');
  const topicsEl = document.getElementById('sidebarMqttTopics');

  if (mqtt.connected) {
    dot.className = 'status-dot online';
    stateEl.textContent = 'Connected';
    stateEl.style.color = '#00FF88';
  } else {
    dot.className = 'status-dot standby';
    stateEl.textContent = 'Local Ready';
    stateEl.style.color = '#7DD3FC';
  }

  brokerEl.textContent = `${mqtt.host || '127.0.0.1'}:${mqtt.port || 1883}`;
  topicsEl.textContent = `${mqtt.registered_nodes || 0} End Node Topics`;

  const activeList = document.getElementById('mqttActiveTopicsList');
  if (activeList) {
    const topics = mqtt.topics || [];
    activeList.innerHTML = topics.length
      ? topics.map((t) => `<div class="server-log-line">${escapeHtml(t)}</div>`).join('')
      : `<div style="color:var(--text-muted);">Belum ada topic End Node terdaftar.</div>`;
  }
}

/* ---------------- REPORTS TAB & CSV EXPORT ---------------- */

function buildReportQueryString() {
  const params = new URLSearchParams();
  const nodeId = document.getElementById('repNodeFilter')?.value;
  const cond = document.getElementById('repConditionFilter')?.value;
  const search = document.getElementById('repSearchInput')?.value?.trim();
  const df = document.getElementById('repDateFrom')?.value;
  const dt = document.getElementById('repDateTo')?.value;

  if (nodeId) params.set('node_id', nodeId);
  if (cond && cond !== 'all') params.set('condition', cond);
  if (search) params.set('search', search);
  if (df) params.set('date_from', df);
  if (dt) params.set('date_to', dt);
  return params.toString();
}

async function loadReportsData() {
  const qs = buildReportQueryString();
  try {
    const res = await fetch(`/api/reports?${qs}`);
    const data = await res.json();
    if (!data.ok) return;
    appState.logs = data.logs || [];
    appState.summaries = data.summaries || [];
    appState.serverLogs = data.server_logs || [];
    renderReportsTab();
  } catch (err) {
    console.error('Failed loading reports:', err);
  }
}

function debouncedReportSearch() {
  clearTimeout(reportSearchTimer);
  reportSearchTimer = setTimeout(() => loadReportsData(), 300);
}

function resetReportFilters() {
  document.getElementById('repNodeFilter').value = '';
  document.getElementById('repConditionFilter').value = 'all';
  document.getElementById('repSearchInput').value = '';
  document.getElementById('repDateFrom').value = '';
  document.getElementById('repDateTo').value = '';
  loadReportsData();
}

function exportReportsCsv() {
  const qs = buildReportQueryString();
  window.location.href = `/api/reports/export.csv?${qs}`;
}

function renderReportsTab() {
  document.getElementById('repTotalRecordsBadge').textContent = `${appState.logs.length} Log`;

  // 1. Node Summary Table
  const sumBody = document.getElementById('nodeSummaryTableBody');
  if (sumBody) {
    sumBody.innerHTML = appState.summaries
      .map(
        (s) => `
        <tr>
          <td><strong>${escapeHtml(s.node_name)}</strong></td>
          <td>${escapeHtml(s.location_name)}</td>
          <td class="mono" style="font-size:11px;color:var(--neon-green);">${escapeHtml(s.mqtt_topic)}</td>
          <td class="mono">${s.total_uplinks || 0}</td>
          <td class="mono" style="color:var(--neon-green);font-weight:700;">${formatNum(s.avg_aci, 3)}</td>
          <td class="mono" style="color:#7DD3FC;font-weight:700;">${formatNum(s.avg_ndsi, 3, true)}</td>
          <td class="mono" style="color:#FF5C77;font-weight:700;">${s.threat_count || 0}</td>
          <td class="mono" style="color:#00FF88;">${s.biodiversity_count || 0}</td>
          <td class="mono" style="color:#38BDF8;">${s.noise_count || 0}</td>
          <td>${renderConditionBadge(s.last_classification, s.last_condition)}</td>
        </tr>
      `
      )
      .join('');
  }

  // 2. Detailed Logs Table
  const logsBody = document.getElementById('reportLogsTableBody');
  if (logsBody) {
    if (appState.logs.length === 0) {
      logsBody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--text-secondary);">Tidak ada data report yang sesuai dengan filter.</td></tr>`;
    } else {
      logsBody.innerHTML = appState.logs
        .map(
          (row) => `
          <tr>
            <td class="mono" style="font-size:11.5px;white-space:nowrap;">${escapeHtml(row.recorded_at)}</td>
            <td><strong>${escapeHtml(row.node_name)}</strong></td>
            <td>${escapeHtml(row.location_name)}</td>
            <td class="mono" style="font-size:11px;color:var(--text-secondary);">${escapeHtml(row.app_id)} / ${escapeHtml(row.dev_eui)}</td>
            <td class="mono" style="color:var(--neon-green);font-weight:700;">${formatNum(row.aci, 3)}</td>
            <td class="mono" style="color:${row.ndsi < 0 ? '#FF6B81' : '#7DD3FC'};font-weight:700;">${formatNum(row.ndsi, 3, true)}</td>
            <td>${renderConditionBadge(row.classification, row.condition)}</td>
            <td class="mono">${Math.round((row.confidence || 0.94) * 100)}%</td>
            <td class="mono" style="font-size:11px;color:var(--text-secondary);">${row.rssi} dBm / ${row.snr} dB</td>
          </tr>
        `
        )
        .join('');
    }
  }

  // 3. Local Server Log Terminal
  const term = document.getElementById('localServerLogTerminal');
  if (term) {
    term.innerHTML = appState.serverLogs.length
      ? appState.serverLogs
          .slice()
          .reverse()
          .map((ln) => {
            const isThreat = ln.includes('[THREAT]');
            return `<div class="server-log-line ${isThreat ? 'threat-line' : ''}">${escapeHtml(ln)}</div>`;
          })
          .join('')
      : `<div style="color:var(--text-muted);">Log server lokal kosong.</div>`;
  }

  // 4. Report Charts
  renderReportCharts();
}

function renderReportCharts() {
  const trendCanvas = document.getElementById('reportTrendChart');
  const classCanvas = document.getElementById('reportClassChart');
  if (!trendCanvas || !classCanvas) return;

  if (reportTrendChart) reportTrendChart.destroy();
  if (reportClassChart) reportClassChart.destroy();

  const chronological = appState.logs.slice(0, 45).reverse();
  reportTrendChart = new Chart(trendCanvas, {
    type: 'line',
    data: {
      labels: chronological.map((r) => `${formatShortTime(r.recorded_at)} ${r.node_name.replace('SylvaSense ', '')}`),
      datasets: [
        {
          label: 'ACI',
          data: chronological.map((r) => r.aci),
          borderColor: '#00FF88',
          backgroundColor: 'rgba(0, 255, 136, 0.14)',
          borderWidth: 2.2,
          pointRadius: 2.5,
          tension: 0.35,
          fill: true,
        },
        {
          label: 'NDSI',
          data: chronological.map((r) => r.ndsi),
          borderColor: '#38BDF8',
          backgroundColor: 'rgba(56, 189, 248, 0.08)',
          borderWidth: 2,
          pointRadius: 2.5,
          tension: 0.35,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#9BBDB0', boxWidth: 12 } },
      },
      scales: {
        x: {
          ticks: { color: '#6E9988', font: { size: 10 }, maxTicksLimit: 8 },
          grid: { color: 'rgba(0,255,136,0.05)' },
        },
        y: {
          ticks: { color: '#6E9988', font: { size: 10.5 } },
          grid: { color: 'rgba(0,255,136,0.07)' },
        },
      },
    },
  });

  reportClassChart = new Chart(classCanvas, {
    type: 'bar',
    data: {
      labels: appState.summaries.map((s) => s.node_name.replace('SylvaSense ', '')),
      datasets: [
        {
          label: 'Ambient Hutan (Biodiversity)',
          data: appState.summaries.map((s) => s.biodiversity_count || 0),
          backgroundColor: 'rgba(0, 255, 136, 0.78)',
          borderRadius: 5,
        },
        {
          label: 'Chainsaw (Threat)',
          data: appState.summaries.map((s) => s.threat_count || 0),
          backgroundColor: 'rgba(255, 42, 75, 0.85)',
          borderRadius: 5,
        },
        {
          label: 'Rain/Wind (Noise)',
          data: appState.summaries.map((s) => s.noise_count || 0),
          backgroundColor: 'rgba(56, 189, 248, 0.78)',
          borderRadius: 5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#9BBDB0', boxWidth: 11, font: { size: 10.5 } } },
      },
      scales: {
        x: {
          stacked: true,
          ticks: { color: '#9BBDB0', font: { size: 10.5 } },
          grid: { display: false },
        },
        y: {
          stacked: true,
          ticks: { color: '#6E9988', precision: 0 },
          grid: { color: 'rgba(0,255,136,0.07)' },
        },
      },
    },
  });
}

/* ---------------- ADD END NODE MODAL & MAP PICKER ---------------- */

function generateRandomNodeKey() {
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  document.getElementById('nodeKeyInput').value = hex;
}

function updateTopicPreview() {
  const appId = (document.getElementById('nodeAppIdInput').value || '<appid>').trim();
  const devEui = (document.getElementById('nodeDevEuiInput').value || '<devui>').trim().toLowerCase();
  document.getElementById('mqttTopicPreview').textContent = `application/${appId}/device/${devEui}/event/up`;
}

function openAddNodeModal() {
  const errEl = document.getElementById('addNodeError');
  errEl.className = 'auth-alert';
  errEl.textContent = '';

  if (!document.getElementById('nodeKeyInput').value) {
    generateRandomNodeKey();
  }
  if (!document.getElementById('nodeDevEuiInput').value) {
    const nextNum = String(appState.nodes.length + 101).padStart(4, '0');
    document.getElementById('nodeDevEuiInput').value = `70b3d57ed006${nextNum}`;
  }
  updateTopicPreview();
  document.getElementById('addNodeModal').classList.add('open');
}

function closeAddNodeModal() {
  document.getElementById('addNodeModal').classList.remove('open');
}

function startMapCoordinatePick() {
  closeAddNodeModal();
  switchMainTab('map');
  appState.pickingCoordinateOnMap = true;
  document.getElementById('pickCoordBanner').classList.add('active');
  document.getElementById('katinganMap').style.cursor = 'crosshair';
}

function cancelMapCoordinatePick(reopenModal = false) {
  appState.pickingCoordinateOnMap = false;
  document.getElementById('pickCoordBanner').classList.remove('active');
  document.getElementById('katinganMap').style.cursor = '';
  if (reopenModal) {
    openAddNodeModal();
  }
}

document.getElementById('addNodeForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = document.getElementById('addNodeError');
  errEl.className = 'auth-alert';

  const payload = {
    name: document.getElementById('nodeNameInput').value,
    location_name: document.getElementById('nodeLocationInput').value,
    latitude: parseFloat(document.getElementById('nodeLatInput').value),
    longitude: parseFloat(document.getElementById('nodeLngInput').value),
    app_id: document.getElementById('nodeAppIdInput').value,
    dev_eui: document.getElementById('nodeDevEuiInput').value,
    node_key: document.getElementById('nodeKeyInput').value,
  };

  try {
    const res = await fetch('/api/nodes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      errEl.textContent = data.error || 'Gagal menambahkan End Node.';
      errEl.className = 'auth-alert error';
      return;
    }

    closeAddNodeModal();
    document.getElementById('nodeNameInput').value = '';
    document.getElementById('nodeLocationInput').value = '';
    document.getElementById('nodeDevEuiInput').value = '';
    generateRandomNodeKey();

    await reloadBootstrapData();
    switchMainTab('map');
    setTimeout(() => selectNodeOnMap(data.node.id, true), 120);
    showToast(`End Node "${data.node.name}" ditambahkan & di-subscribe ke MQTT!`);
  } catch (err) {
    errEl.textContent = 'Gagal menghubungi server lokal.';
    errEl.className = 'auth-alert error';
  }
});

async function confirmDeleteNode(nodeId, nodeName) {
  if (!confirm(`Hapus End Node "${nodeName}" beserta topic MQTT-nya dari database?`)) return;
  try {
    const res = await fetch(`/api/nodes/${nodeId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      appState.selectedNodeId = null;
      await reloadBootstrapData();
      if (appState.nodes.length > 0) {
        selectNodeOnMap(appState.nodes[0].id, false);
      } else {
        document.getElementById('nodeDetailContainer').innerHTML =
          `<div style="padding:24px;text-align:center;color:var(--text-secondary);">Belum ada End Node terdaftar.</div>`;
      }
      showToast(`End Node "${nodeName}" dihapus.`);
    }
  } catch (err) {
    console.error(err);
  }
}

/* ---------------- MQTT SETTINGS & PACKET SIMULATION ---------------- */

async function openMqttModal() {
  document.getElementById('mqttModal').classList.add('open');
  try {
    const res = await fetch('/api/mqtt/settings');
    const data = await res.json();
    if (data.ok) {
      document.getElementById('mqttHostInput').value = data.settings.mqtt_host;
      document.getElementById('mqttPortInput').value = data.settings.mqtt_port;
      document.getElementById('mqttUserInput').value = data.settings.mqtt_username || '';
      document.getElementById('mqttPassInput').value = data.settings.mqtt_password || '';
      document.getElementById('mqttStrictKeyCheck').checked = Boolean(data.settings.enforce_node_key);
      updateMqttStatusUi(data.status);
    }
  } catch (err) {
    console.error(err);
  }
}

function closeMqttModal() {
  document.getElementById('mqttModal').classList.remove('open');
}

document.getElementById('mqttSettingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    mqtt_host: document.getElementById('mqttHostInput').value,
    mqtt_port: parseInt(document.getElementById('mqttPortInput').value, 10),
    mqtt_username: document.getElementById('mqttUserInput').value,
    mqtt_password: document.getElementById('mqttPassInput').value,
    enforce_node_key: document.getElementById('mqttStrictKeyCheck').checked,
  };

  try {
    const res = await fetch('/api/mqtt/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      updateMqttStatusUi(data.status);
      showToast(`Konfigurasi MQTT diperbarui ke ${payload.mqtt_host}:${payload.mqtt_port}`);
    }
  } catch (err) {
    showToast('Gagal menyimpan pengaturan MQTT.', true);
  }
});

async function simulateNodePacket(nodeId, preset) {
  try {
    const res = await fetch(`/api/nodes/${nodeId}/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast(data.error || 'Gagal mengirim paket uji.', true);
    }
  } catch (err) {
    showToast('Gagal menghubungi server lokal.', true);
  }
}

function triggerQuickSimulation(preset) {
  const nodeId = document.getElementById('simTargetNodeSelect').value;
  if (!nodeId) return;
  simulateNodePacket(Number(nodeId), preset);
}

/* ---------------- REAL-TIME SERVER-SENT EVENTS (SSE) ---------------- */

function connectRealtimeStream() {
  const es = new EventSource('/api/stream');
  es.onmessage = async (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.event === 'mqtt_status' || msg.event === 'connected') {
        updateMqttStatusUi(msg.data);
      } else if (msg.event === 'telemetry') {
        const { log, node, mqtt } = msg.data;
        if (mqtt) updateMqttStatusUi(mqtt);

        // Update node in local state
        const idx = appState.nodes.findIndex((n) => n.id === node.id);
        if (idx >= 0) appState.nodes[idx] = node;
        else appState.nodes.push(node);

        const selectedFilterNode = document.getElementById('overviewNodeFilter')?.value;
        if (!selectedFilterNode || Number(selectedFilterNode) === node.id) {
          appState.timeseries.push(log);
          if (appState.timeseries.length > 48) appState.timeseries.shift();
          updateOverviewCharts(appState.timeseries);
        }

        refreshKpisAndSelectors();
        renderOverviewMapNodes();
        renderMapNodes();

        if (appState.selectedNodeId === node.id) {
          selectNodeOnMap(node.id, false);
        }
        if (appState.activeTab === 'reports') {
          loadReportsData();
        }

        const isThreat = (log.condition || '').toLowerCase() === 'threat';
        showToast(
          `[${node.name}] ${log.classification} (${log.condition}) • ACI ${formatNum(log.aci, 2)} | NDSI ${formatNum(log.ndsi, 2, true)}`,
          isThreat
        );
      } else if (msg.event === 'node_added' || msg.event === 'node_deleted') {
        await reloadBootstrapData();
      }
    } catch (err) {
      console.error('SSE parse error:', err);
    }
  };
}

/* ---------------- INITIAL BOOTSTRAP ---------------- */

async function reloadBootstrapData() {
  const res = await fetch('/api/bootstrap');
  if (res.status === 401) {
    window.location.href = '/login';
    return;
  }
  const data = await res.json();
  if (!data.ok) return;

  appState.nodes = data.nodes || [];
  appState.timeseries = data.timeseries || [];
  appState.summaries = data.summaries || [];
  appState.logs = data.logs || [];
  appState.serverLogs = data.server_logs || [];

  refreshKpisAndSelectors();
  updateMqttStatusUi(data.mqtt);
  renderOverviewMapNodes();
  renderMapNodes();
  updateOverviewCharts(appState.timeseries);

  if (appState.nodes.length > 0 && !appState.selectedNodeId) {
    appState.selectedNodeId = appState.nodes[0].id;
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  startClock();
  initMaps();
  initOverviewCharts();
  await reloadBootstrapData();
  connectRealtimeStream();
});
