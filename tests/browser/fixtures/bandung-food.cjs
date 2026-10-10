/* A renderer verification fixture for a non-US map, not a prerecorded agent response. */
const places = [
  { name: 'Susu Murni Idjan', lat: -6.9312, lng: 107.6103 },
  { name: 'Ronde Jahe Gardujati', lat: -6.9178, lng: 107.5977 },
  { name: 'Lotek Mahmud', lat: -6.9349, lng: 107.6219 },
];
module.exports = {
  type: 'sandbox', title: 'Bandung food map', initialHeight: 420,
  html: '<div id="bandung" style="height:320px"></div><p id="bandung-status">Loading map…</p>',
  jsFunctions: `const places=${JSON.stringify(places)};
async function setup(){
 const status=document.getElementById('bandung-status');
 try{
  const L=await import('leaflet');
  const map=L.map('bandung',{scrollWheelZoom:false}).fitBounds(places.map(p=>[p.lat,p.lng]),{padding:[24,24]});
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',{subdomains:'abcd',maxZoom:19,attribution:'© OpenStreetMap contributors © CARTO'}).addTo(map);
  places.forEach(p=>L.circleMarker([p.lat,p.lng],{radius:6}).bindTooltip(p.name).addTo(map));
  window.bandungMap=map;status.textContent='Map loaded';
 }catch(error){status.textContent='Map unavailable: '+error.message;status.dataset.failed='1';}
}
`,
  jsExpressions: 'setup();',
};
