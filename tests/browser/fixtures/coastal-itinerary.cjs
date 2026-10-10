/* A renderer verification fixture, not a prerecorded agent response. */
const photos = require('./coastal-photos.json');
const stops = [
  { name: 'San Francisco', day: 'Day 1 · Start', lat: 37.8078, lng: -122.475, description: 'Begin at the Golden Gate.', ...photos[0] },
  { name: 'Monterey', day: 'Day 1 · Overnight', lat: 36.6166, lng: -121.9018, description: 'Explore Cannery Row and the bay.', ...photos[1] },
  { name: 'Big Sur', day: 'Day 2 · Scenic stop', lat: 36.2992, lng: -121.8734, description: 'A dramatic stretch of coastline.', ...photos[2] },
  { name: 'Santa Barbara', day: 'Day 3 · Overnight', lat: 34.4381, lng: -119.7137, description: 'Mission architecture and the waterfront.', ...photos[3] },
  { name: 'Los Angeles', day: 'Day 4 · Finish', lat: 34.1341, lng: -118.3215, description: 'Finish with the Hollywood hills.', ...photos[4] },
];
const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
module.exports = {
  type: 'sandbox', title: 'California coast · 4 days', initialHeight: 720,
  html: `<h2>California coast · 4 days</h2><p class="subtitle">San Francisco → Monterey → Big Sur → Santa Barbara → Los Angeles</p><p class="meta">4 days <span>5 stops</span> Proposed itinerary</p><div id="map" aria-label="Map of five California coast stops"></div><div class="playback"><button id="pause" disabled>Pause</button><button id="replay" disabled>Replay</button><span id="current" aria-live="polite"></span></div><p id="map-status" role="status">Loading live map…</p><div class="stops">${stops.map((stop, index) => `<section class="stop" data-stop="${index}"><button class="select-stop" data-index="${index}" aria-label="Select ${esc(stop.name)}"><img src="${esc(stop.image_url)}" alt="${esc(stop.place)}" loading="eager"><span class="day">${esc(stop.day)}</span><strong>${index + 1}. ${esc(stop.name)}</strong><span class="description">${esc(stop.description)}</span></button><a class="credit" href="${esc(stop.credit_url)}">Photo: ${esc(stop.artist)} · ${esc(stop.license)}</a></section>`).join('')}</div><p class="note">Illustrative itinerary connections — not verified driving directions. Stop coordinates are approximate. Live USGS tiles; photos are sourced from Wikimedia Commons.</p>`,
  css: `h2{font-size:23px;margin:0 0 8px;letter-spacing:-.4px}.subtitle,.meta{color:var(--color-text-secondary);font-size:12px;margin:0 0 12px}.meta{display:flex;gap:20px;font-size:11px}#map{height:300px;width:100%;background:var(--color-background-secondary)}.playback{display:flex;align-items:center;gap:8px;margin:12px 0}.playback button{font:inherit;font-size:11px;padding:5px 12px;cursor:pointer}.playback span,#map-status{font-size:11px;color:var(--color-text-secondary)}#map-status{margin:0 0 12px}.stops{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px}.stop{min-width:0}.select-stop{display:flex;flex-direction:column;gap:6px;width:100%;text-align:left;border:0;border-radius:0;background:transparent!important;padding:0!important;color:var(--color-text-primary);cursor:pointer;white-space:normal}.select-stop img{width:100%;height:95px;object-fit:cover;filter:saturate(.65);margin-bottom:4px}.day{font-size:9px;text-transform:uppercase;color:var(--color-text-tertiary)}.stop strong{font-size:13px;line-height:1.4}.description{font-size:11px;line-height:1.5;color:var(--color-text-secondary)}.credit{display:block;font-size:9px;line-height:1.5;margin-top:10px;color:var(--color-text-tertiary)}.stop.active .select-stop img{outline:2px solid #d09c62;outline-offset:2px}.pin-dot{display:flex;align-items:center;justify-content:center;width:24px;height:24px;border:2px solid white;border-radius:50%;background:#242424;color:white;font:bold 12px system-ui;box-shadow:0 1px 6px #0005;opacity:0}.pin-dot.active{background:#a86731}.leaflet-div-icon{border:0;background:none}.leaflet-tile-pane{filter:grayscale(.8)}.note{font-size:10px;line-height:1.6;color:var(--color-text-tertiary);margin-top:16px}@media(max-width:600px){#map{height:280px}.stops{display:flex;overflow-x:auto;padding:4px 3px 12px;scroll-snap-type:x proximity}.stop{flex:0 0 150px;scroll-snap-align:start}.select-stop img{height:100px}.subtitle{line-height:1.7}}`,
  jsFunctions: `const stops=${JSON.stringify(stops.map(({name, lat, lng}) => ({name, lat, lng})))};
async function setup(){
 const status=document.getElementById('map-status');
 try {
  const L=await window.loadLeaflet();
  const map=L.map('map',{scrollWheelZoom:false,attributionControl:true}).fitBounds(stops.map(s=>[s.lat,s.lng]),{padding:[28,28]});
  window.tripMap=map;
  let loaded=0,failed=0;
  const tiles=L.tileLayer('https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',{maxNativeZoom:16,maxZoom:18,attribution:'<a href="https://www.usgs.gov/programs/national-geospatial-program/national-map">USGS The National Map</a>'});
  tiles.on('tileload',()=>{loaded++;status.textContent=failed?'Some map tiles could not load.':'Live USGS map loaded';status.dataset.loaded=String(loaded);});
  tiles.on('tileerror',()=>{failed++;status.textContent='Some map tiles could not load. Check your connection.';status.dataset.failed=String(failed);});
  tiles.addTo(map);
  const markers=stops.map((s,i)=>L.marker([s.lat,s.lng],{icon:L.divIcon({className:'numbered-pin',html:'<span class="pin-dot">'+(i+1)+'</span>',iconSize:[24,24],iconAnchor:[12,12]}),keyboard:true,title:s.name}).addTo(map));
  const pins=markers.map(marker=>marker.getElement().querySelector('.pin-dot'));
  const line=L.polyline([],{color:'#5f5b51',weight:2,dashArray:'4 6',interactive:false}).addTo(map);
  let state='paused';
  const controller=createTripAnimator({stopCount:stops.length,durationMs:6000,pinElements:pins,onFrame(frame){
   document.querySelectorAll('.stop').forEach((el,i)=>el.classList.toggle('active',i===frame.activeStop));
   pins.forEach((el,i)=>el.classList.toggle('active',i===frame.activeStop));
   document.getElementById('current').textContent='Stop '+(frame.activeStop+1)+' of '+stops.length+' · '+stops[frame.activeStop].name;
   line.setLatLngs(stops.slice(0,frame.pinIndex+(frame.pinProgress>0?1:0)).map(s=>[s.lat,s.lng]));
  },onState(value){state=value;document.getElementById('pause').textContent=value==='playing'?'Pause':'Play';document.getElementById('pause').disabled=value==='complete';document.getElementById('map').dataset.state=value;}});
  window.tripController=controller;
  markers.forEach((marker,i)=>marker.on('click',()=>controller.seek(i)));
  document.querySelectorAll('.select-stop').forEach(button=>button.addEventListener('click',()=>controller.seek(Number(button.dataset.index))));
  document.getElementById('pause').addEventListener('click',()=>state==='playing'?controller.pause():controller.play());
  document.getElementById('replay').addEventListener('click',()=>controller.replay());
  document.getElementById('pause').disabled=false;document.getElementById('replay').disabled=false;
  const observer=new ResizeObserver(()=>map.invalidateSize());observer.observe(document.getElementById('map'));
  window.addEventListener('pagehide',()=>{observer.disconnect();controller.dispose();map.remove();});
  map.invalidateSize();controller.play();
 }catch(error){status.textContent='Map unavailable: '+error.message;status.dataset.failed='1';}
}
`,
  jsExpressions: 'setup();',
};
