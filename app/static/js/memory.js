(() => {
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('memoryGraph');
  const chart = document.getElementById('memoryChart');
  const detail = document.getElementById('memoryDetail');
  const indexList = document.getElementById('memoryIndex');
  const slider = document.getElementById('memoryScrubber');
  const dateLabel = document.getElementById('memoryDate');
  const bullets = document.getElementById('memoryBullets');
  let graph = {nodes: [], edges: [], days: [], types: []};
  let points = new Map();
  let selected = null;
  let requestNumber = 0;
  let yaw = -.38;
  let pitch = -.32;
  let zoom = 1;
  let dragging = false;
  let dragStart = null;
  let ignoreClickUntil = 0;
  let framePending = false;

  function updateViewBox() {
    svg.setAttribute('viewBox', window.matchMedia('(max-width: 650px)').matches
      ? '175 0 650 660' : '0 0 1000 660');
  }
  updateViewBox();
  window.addEventListener('resize', updateViewBox);

  function svgEl(tag, attributes = {}, parent = svg) {
    const element = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
    parent.append(element);
    return element;
  }

  function star(x, y, facts, radius, parent = svg) {
    const spokes = Math.max(4, Math.min(12, facts || 4));
    let path = '';
    for (let i = 0; i < spokes * 2; i++) {
      const angle = i * Math.PI / spokes - Math.PI / 2;
      const length = i % 2 ? radius * .33 : radius;
      path += `${i ? 'L' : 'M'}${(x + Math.cos(angle) * length).toFixed(1)},${(y + Math.sin(angle) * length).toFixed(1)}`;
    }
    svgEl('path', {d: path + 'Z', class: 'herbarium-star'}, parent);
    svgEl('circle', {cx: x, cy: y, r: 2.1, class: 'herbarium-star-center'}, parent);
  }

  function rotate3D(point) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const x = point.x * cy + point.z * sy;
    const z = -point.x * sy + point.z * cy;
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    return {x, y: point.y * cp - z * sp, z: point.y * sp + z * cp};
  }

  function project(point) {
    const rotated = rotate3D(point);
    const scale = 700 / (700 - rotated.z) * zoom;
    return {x: 500 + rotated.x * scale, y: 330 + rotated.y * scale, z: rotated.z, scale};
  }

  function planet3D(index, count) {
    const angle = index * 2 * Math.PI / count - Math.PI / 2;
    return {x: Math.cos(angle) * 250, y: Math.sin(angle) * 210, z: Math.sin(angle) * 115, angle};
  }

  function planet(type, index, count) {
    return project(planet3D(index, count));
  }

  function entityPoint(node) {
    const typeIndex = graph.types.indexOf(node.type);
    const center = planet3D(typeIndex, graph.types.length);
    const siblings = graph.nodes.filter(item => item.type === node.type);
    const place = siblings.findIndex(item => item.id === node.id);
    const angle = center.angle + Math.PI + place * 2 * Math.PI / siblings.length;
    return project({x: center.x + Math.cos(angle) * 64,
      y: center.y + Math.sin(angle) * 50, z: center.z + Math.sin(angle) * 70});
  }

  function orbitPath(samples, pointAt) {
    let path = '';
    for (let i = 0; i <= samples; i++) {
      const point = project(pointAt(i * 2 * Math.PI / samples));
      path += `${i ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`;
    }
    return path + 'Z';
  }

  function depthOrbit(samples, pointAt, className) {
    let behind = '', ahead = '';
    for (let i = 0; i < samples; i++) {
      const a = project(pointAt(i * 2 * Math.PI / samples));
      const b = project(pointAt((i + 1) * 2 * Math.PI / samples));
      const segment = `M${a.x.toFixed(1)},${a.y.toFixed(1)}L${b.x.toFixed(1)},${b.y.toFixed(1)}`;
      if ((a.z + b.z) / 2 < 0) behind += segment;
      else ahead += segment;
    }
    svgEl('path', {d: behind, class: `${className} is-back`});
    svgEl('path', {d: ahead, class: `${className} is-front`});
  }

  function drawCosmos() {
    const defs = svgEl('defs');
    const planetLight = svgEl('radialGradient', {id: 'memory-planet-light', cx: '30%', cy: '24%', r: '75%'}, defs);
    svgEl('stop', {offset: '0%', 'stop-color': '#7cf2c4', 'stop-opacity': '.78'}, planetLight);
    svgEl('stop', {offset: '43%', 'stop-color': '#7cf2c4', 'stop-opacity': '.2'}, planetLight);
    svgEl('stop', {offset: '100%', 'stop-color': '#7cf2c4', 'stop-opacity': '.03'}, planetLight);
    const gravity = svgEl('radialGradient', {id: 'memory-gravity'}, defs);
    svgEl('stop', {offset: '0%', 'stop-color': '#7cf2c4', 'stop-opacity': '.28'}, gravity);
    svgEl('stop', {offset: '100%', 'stop-color': '#7cf2c4', 'stop-opacity': '0'}, gravity);
    let seed = 163;
    const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    for (let i = 0; i < 145; i++) {
      const x = 35 + random() * 930, y = 42 + random() * 560;
      const radius = random() > .91 ? 1.35 : .3 + random() * .55;
      svgEl('circle', {cx: x.toFixed(1), cy: y.toFixed(1), r: radius.toFixed(2), class: 'herbarium-distant-star', opacity: (.16 + random() * .62).toFixed(2)});
    }
    svgEl('circle', {cx: 500, cy: 330, r: 125, fill: 'url(#memory-gravity)'});
  }

  function drawGuides() {
    depthOrbit(120, a => ({x: Math.cos(a) * 250, y: Math.sin(a) * 210, z: Math.sin(a) * 115}), 'herbarium-orbit');
    depthOrbit(120, a => ({x: Math.cos(a) * 310, y: Math.sin(a) * 255, z: Math.sin(a) * 145}), 'herbarium-orbit herbarium-orbit-outer');
    depthOrbit(120, a => ({x: Math.cos(a) * 174, y: Math.sin(a) * 145, z: Math.sin(a) * 80}), 'herbarium-orbit herbarium-orbit-inner');
    depthOrbit(80, a => ({x: Math.cos(a) * 78, y: Math.sin(a) * 24, z: Math.sin(a) * 27}), 'herbarium-gravity-ring');
    const center = project({x: 0, y: 0, z: 0});
    svgEl('circle', {cx: center.x, cy: center.y, r: 45 * center.scale, class: 'herbarium-gravity-core'});
    graph.types.forEach((type, i) => {
      const base = planet3D(i, graph.types.length);
      depthOrbit(48, a => ({x: base.x + Math.cos(a) * 64,
        y: base.y + Math.sin(a) * 50, z: base.z + Math.sin(a) * 70}), 'herbarium-planet-orbit');
    });
  }

  function drawEdges() {
    graph.edges.forEach((edge, i) => {
      const from = points.get(edge.src), to = points.get(edge.dst_resolved);
      if (!from || !to) return;
      const left = from.x <= to.x ? from : to;
      const right = from.x <= to.x ? to : from;
      const middleX = (left.x + right.x) / 2;
      const middleY = (left.y + right.y) / 2 - Math.min(45, Math.abs(right.x - left.x) * .15);
      const id = `memory-edge-${i}`;
      svgEl('path', {
        id, d: `M${left.x},${left.y} Q${middleX},${middleY} ${right.x},${right.y}`,
        class: 'herbarium-edge', 'data-src': edge.src, 'data-dst': edge.dst_resolved
      });
      const label = svgEl('text', {class: 'herbarium-edge-label'});
      const textPath = svgEl('textPath', {href: '#' + id, startOffset: '50%', 'text-anchor': 'middle'}, label);
      textPath.textContent = edge.dst;
    });
  }

  function markSelected(id) {
    selected = id;
    svg.querySelectorAll('.herbarium-node[data-id]').forEach(node => node.classList.toggle('is-selected', node.dataset.id === id));
    indexList.querySelectorAll('[data-id]').forEach(node => node.classList.toggle('is-selected', node.dataset.id === id));
  }

  function highlightEdges(id, on) {
    svg.querySelectorAll('.herbarium-edge').forEach(edge => {
      edge.classList.toggle('is-active', on && (edge.dataset.src === id || edge.dataset.dst === id));
    });
  }

  function drawNodes() {
    const bodies = graph.types.map((type, i) => ({kind: 'planet', type, index: i,
      point: planet(type, i, graph.types.length)}));
    graph.nodes.forEach((node, i) => bodies.push({kind: 'entity', node, index: i, point: points.get(node.id)}));
    bodies.push({kind: 'center', point: project({x: 0, y: 0, z: 0})});
    bodies.sort((a, b) => a.point.z - b.point.z);
    bodies.forEach(body => {
      const p = body.point;
      const depthOpacity = Math.max(.54, Math.min(1, .77 + p.z / 750));
      if (body.kind === 'planet') {
        const base = planet3D(body.index, graph.types.length);
        const group = svgEl('g', {opacity: depthOpacity.toFixed(2)});
        svgEl('path', {d: orbitPath(48, a => ({x: base.x + Math.cos(a) * 28,
          y: base.y + Math.sin(a) * 7, z: base.z + Math.sin(a) * 14})),
          class: 'herbarium-planet-ring'}, group);
        svgEl('circle', {cx: p.x, cy: p.y, r: (16 * p.scale).toFixed(1), class: 'herbarium-planet-disc'}, group);
        star(p.x, p.y, 6, 4.5 * p.scale, group);
        const label = svgEl('text', {x: p.x, y: p.y + 31 * p.scale,
          class: 'herbarium-type-label'}, group);
        label.textContent = body.type.toUpperCase();
      } else if (body.kind === 'entity') {
        const node = body.node;
        const group = svgEl('g', {class: 'herbarium-node', tabindex: '0', role: 'button',
          'aria-label': `${node.title}, ${node.facts} facts`, 'data-id': node.id,
          opacity: depthOpacity.toFixed(2)});
        svgEl('circle', {cx: p.x, cy: p.y, r: (27 * p.scale).toFixed(1),
          class: 'herbarium-entity-halo'}, group);
        star(p.x, p.y, node.facts, Math.min(27, 16 + node.backlinks * 4) * p.scale, group);
        const label = svgEl('text', {x: p.x, y: p.y + 34 * p.scale,
          class: 'herbarium-node-label'}, group);
        label.textContent = node.title;
        const number = svgEl('text', {x: p.x, y: p.y - 28 * p.scale,
          class: 'herbarium-node-number'}, group);
        number.textContent = String(body.index + 1).padStart(2, '0');
        group.addEventListener('click', event => { if (Date.now() >= ignoreClickUntil) openNode(node); event.stopPropagation(); });
        group.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openNode(node); }
        });
        group.addEventListener('mouseenter', () => highlightEdges(node.id, true));
        group.addEventListener('mouseleave', () => highlightEdges(node.id, false));
        group.addEventListener('focus', () => highlightEdges(node.id, true));
        group.addEventListener('blur', () => highlightEdges(node.id, false));
      } else {
        const group = svgEl('g', {class: 'herbarium-node', tabindex: '0',
          'aria-label': 'You, the center of this 3D memory universe'});
        star(p.x, p.y, 8, 28 * p.scale, group);
        const label = svgEl('text', {x: p.x, y: p.y + 42 * p.scale,
          class: 'herbarium-node-label'}, group);
        label.textContent = 'YOU';
      }
    });
  }

  function drawIndex() {
    indexList.replaceChildren();
    if (!graph.nodes.length) {
      const empty = document.createElement('p');
      empty.className = 'herbarium-ledger-empty';
      empty.textContent = 'No specimens collected yet.';
      indexList.append(empty);
      return;
    }
    graph.nodes.forEach((node, i) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'herbarium-ledger-item';
      button.dataset.id = node.id;
      button.innerHTML = `<span class="herbarium-index-num">${String(i + 1).padStart(2, '0')}</span><span class="herbarium-index-name"></span><span class="herbarium-index-mark">✳</span>`;
      const name = button.querySelector('.herbarium-index-name');
      name.textContent = node.title;
      const type = document.createElement('small');
      type.textContent = node.type.toUpperCase();
      name.append(type);
      button.addEventListener('click', () => openNode(node));
      indexList.append(button);
    });
  }

  function render(updateIndex = true) {
    svg.replaceChildren();
    detail.hidden = true;
    selected = null;
    points = new Map(graph.nodes.map(node => [node.id, entityPoint(node)]));
    drawCosmos();
    if (graph.nodes.length) drawGuides();
    drawEdges();
    drawNodes();
    if (updateIndex) drawIndex();
    else indexList.querySelectorAll('.is-selected').forEach(item => item.classList.remove('is-selected'));
    document.getElementById('memoryEmpty').hidden = graph.nodes.length > 0;
    document.getElementById('memoryCount').textContent = String(graph.nodes.length).padStart(2, '0');
    document.getElementById('memoryGraphMeta').textContent = `${String(graph.edges.length).padStart(2, '0')} CONSTELLATIONS`;
  }

  function scheduleCameraRender() {
    if (framePending) return;
    framePending = true;
    requestAnimationFrame(() => { framePending = false; render(false); });
  }

  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    dragging = true;
    dragStart = {x: event.clientX, y: event.clientY, yaw, pitch, moved: false};
    svg.classList.add('is-dragging');
  });
  document.addEventListener('pointermove', event => {
    if (!dragging || !dragStart) return;
    const dx = event.clientX - dragStart.x, dy = event.clientY - dragStart.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) dragStart.moved = true;
    if (!dragStart.moved) return;
    yaw = dragStart.yaw + dx * .007;
    pitch = Math.max(-1.25, Math.min(1.25, dragStart.pitch + dy * .007));
    scheduleCameraRender();
  });
  document.addEventListener('pointerup', () => {
    if (!dragging) return;
    dragging = false;
    svg.classList.remove('is-dragging');
    if (dragStart?.moved) ignoreClickUntil = Date.now() + 250;
    dragStart = null;
  });
  svg.addEventListener('wheel', event => {
    event.preventDefault();
    zoom = Math.max(.7, Math.min(1.45, zoom * (event.deltaY > 0 ? .92 : 1.08)));
    scheduleCameraRender();
  }, {passive: false});
  document.getElementById('memoryCameraReset').addEventListener('click', () => {
    yaw = -.38; pitch = -.32; zoom = 1; render(false);
  });

  function appendFactText(container, text) {
    let cursor = 0;
    for (const match of text.matchAll(/\[\[(\d{4}-\d{2}-\d{2})(?:#[^\]]+)?\]\]/g)) {
      container.append(document.createTextNode(text.slice(cursor, match.index)));
      const link = document.createElement('a');
      link.href = '#';
      link.textContent = match[0];
      link.addEventListener('click', event => {
        event.preventDefault();
        const at = graph.days.indexOf(match[1]);
        if (at >= 0) { slider.value = String(at); slider.dispatchEvent(new Event('input')); }
      });
      container.append(link);
      cursor = match.index + match[0].length;
    }
    container.append(document.createTextNode(text.slice(cursor)));
  }

  function positionDetail(point) {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const screen = new DOMPoint(point.x, point.y).matrixTransform(matrix);
    const frame = chart.getBoundingClientRect();
    const width = detail.offsetWidth, height = detail.offsetHeight;
    const left = Math.min(frame.width - width - 12, Math.max(12, screen.x - frame.left - width / 2));
    let top = screen.y - frame.top + 28;
    if (top + height > frame.height - 34) top = screen.y - frame.top - height - 28;
    detail.style.left = `${left}px`;
    detail.style.top = `${Math.max(48, top)}px`;
  }

  async function openNode(node) {
    const response = await fetch(`/api/memory/entity/${encodeURIComponent(node.type)}/${encodeURIComponent(node.slug)}`);
    if (!response.ok) return;
    const data = await response.json();
    const point = points.get(node.id);
    if (!point) return;
    markSelected(node.id);
    svg.querySelectorAll('.herbarium-fact-moon').forEach(moon => moon.remove());
    data.facts.forEach((_, i) => {
      const angle = i * 2 * Math.PI / data.facts.length - Math.PI / 2;
      svgEl('circle', {cx: (point.x + Math.cos(angle) * 50).toFixed(1), cy: (point.y + Math.sin(angle) * 50).toFixed(1), r: 3, class: 'herbarium-fact-moon'});
    });
    detail.replaceChildren();
    const close = document.createElement('button');
    close.className = 'detail-close';
    close.textContent = '× CLOSE';
    close.addEventListener('click', () => { detail.hidden = true; markSelected(null); svg.querySelectorAll('.herbarium-fact-moon').forEach(moon => moon.remove()); });
    detail.append(close);
    const title = document.createElement('h2');
    title.textContent = data.title;
    detail.append(title);
    const sub = document.createElement('p');
    sub.className = 'detail-sub';
    sub.textContent = `${node.type.toUpperCase()} / ${String(data.facts.length).padStart(2, '0')} FACTS`;
    detail.append(sub);
    data.facts.forEach((fact, i) => {
      const row = document.createElement('p');
      row.className = 'fact';
      const superseded = fact.startsWith('~~');
      if (superseded) row.classList.add('is-superseded');
      appendFactText(row, superseded ? fact.replace(/^~~/, '').replace(/~~/, '') : fact);
      if (!superseded) {
        const forget = document.createElement('button');
        forget.className = 'forget';
        forget.textContent = 'FORGET';
        forget.setAttribute('aria-label', `Forget fact ${i + 1}`);
        forget.addEventListener('click', async () => {
          if (!confirm('Forget this fact?')) return;
          const result = await fetch(`/api/memory/entity/${encodeURIComponent(node.type)}/${encodeURIComponent(node.slug)}/forget`, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({number: i})
          });
          if (result.ok) { await loadGraph(graph.days[Number(slider.value)] || ''); await openNode(node); }
        });
        row.append(forget);
      }
      detail.append(row);
    });
    const rawButton = document.createElement('button');
    rawButton.textContent = 'OPEN RAW .MD ↗';
    const raw = document.createElement('pre');
    raw.className = 'raw';
    raw.hidden = true;
    raw.textContent = data.raw;
    rawButton.addEventListener('click', () => { raw.hidden = !raw.hidden; positionDetail(point); });
    detail.append(rawButton, raw);
    detail.hidden = false;
    positionDetail(point);
  }

  async function loadGraph(until = '') {
    const ticket = ++requestNumber;
    const url = '/api/memory/graph' + (until ? '?until=' + encodeURIComponent(until) : '');
    const response = await fetch(url);
    if (!response.ok) throw new Error('Could not load memory graph');
    const data = await response.json();
    if (ticket !== requestNumber) return;
    graph = data;
    slider.max = String(Math.max(0, graph.days.length - 1));
    render();
  }

  async function showTimeline(day) {
    dateLabel.textContent = day || 'NO RECORDS YET';
    bullets.replaceChildren();
    if (!day) return;
    const response = await fetch('/api/memory/timeline?date=' + encodeURIComponent(day));
    if (!response.ok) return;
    const data = await response.json();
    if (dateLabel.textContent !== day) return;
    data.blocks.slice(-4).forEach(text => {
      const note = document.createElement('p');
      note.textContent = '§ ' + text;
      bullets.append(note);
    });
  }

  slider.addEventListener('input', async () => {
    const current = Number(slider.value);
    const day = graph.days[current] || '';
    await Promise.all([loadGraph(day), showTimeline(day)]);
    slider.value = String(current);
  });

  loadGraph().then(() => {
    const latest = graph.days.at(-1);
    if (latest) { slider.value = String(graph.days.length - 1); slider.dispatchEvent(new Event('input')); }
  }).catch(() => {
    document.getElementById('memoryEmpty').hidden = false;
  });
})();
