/* Three.js renderer fixture for the OpenIntelligentUI aircraft scene. */
module.exports = {
  type: 'sandbox', title: 'Pitch, roll & yaw', initialHeight: 650,
  html: `<h2>Pitch, roll & yaw</h2><p class="intro">Drag to orbit the airplane. Move a slider to explore one axis.</p><div id="scene" aria-label="Interactive 3D airplane"><span class="axis-label" id="label-pitch">Pitch</span><span class="axis-label" id="label-roll">Roll</span><span class="axis-label" id="label-yaw">Yaw</span></div><p id="status" role="status">Loading the 3D scene…</p><div class="controls">${[['pitch', 'Raise or lower the nose · lateral axis'], ['roll', 'Bank the wings · longitudinal axis'], ['yaw', 'Turn the nose · vertical axis']].map(([axis, description]) => `<label class="axis-control ${axis}"><strong>${axis[0].toUpperCase() + axis.slice(1)} <output id="${axis}-value">0°</output></strong><span>${description}</span><input id="${axis}" type="range" min="-60" max="60" value="0"><button type="button" data-demo="${axis}">Demonstrate ${axis}</button></label>`).join('')}</div><button type="button" id="reset">Reset view</button><p class="note">Conceptual rotation model, not a flight simulator. Blue: pitch · green: roll · amber: yaw.</p>`,
  css: `h2{font-size:23px;margin:0 0 10px}.intro,.note{font-size:12px;line-height:1.6;color:var(--color-text-secondary)}#scene{height:300px;position:relative;touch-action:none}#scene canvas{display:block;width:100%;height:100%}.axis-label{position:absolute;font-size:11px;font-weight:600;pointer-events:none}.controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;margin:20px 0}.axis-control{display:flex;flex-direction:column;gap:10px;min-width:0}.axis-control strong{display:flex;justify-content:space-between;font-size:13px}.axis-control span{font-size:11px;line-height:1.5;color:var(--color-text-secondary)}.axis-control input{width:100%;min-width:0;accent-color:#679ccc}.axis-control button,#reset{font:inherit;font-size:11px;padding:6px 12px;cursor:pointer}.pitch strong,#label-pitch{color:#7cadd8}.roll strong,#label-roll{color:#80b79b}.yaw strong,#label-yaw{color:#dfb56b}#status{font-size:11px;color:var(--color-text-secondary);margin:8px 0}@media(max-width:500px){.controls{grid-template-columns:1fr;gap:20px}#scene{height:240px}}`,
  jsFunctions: `async function setupAircraft(){
 const status=document.getElementById('status');
 try{
  const THREE=await import('three');
  const {OrbitControls}=await import('three/examples/jsm/controls/OrbitControls.js');
  const host=document.getElementById('scene');
  const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setClearColor(0x000000,0);host.appendChild(renderer.domElement);
  const scene=new THREE.Scene();const camera=new THREE.PerspectiveCamera(38,1,.1,100);camera.position.set(6,4,7);
  const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.enableZoom=false;controls.enablePan=false;
  scene.add(new THREE.AmbientLight(0xffffff,2));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(4,8,6);scene.add(light);
  const plane=new THREE.Group();scene.add(plane);
  const bodyMat=new THREE.MeshStandardMaterial({color:0x82b3cd,roughness:.55});const wingMat=new THREE.MeshStandardMaterial({color:0xb8d0da,roughness:.6});const cockpitMat=new THREE.MeshStandardMaterial({color:0x244f67,roughness:.2});
  const body=new THREE.Mesh(new THREE.CapsuleGeometry(.2,2.5,8,24),bodyMat);body.rotation.z=-Math.PI/2;plane.add(body);
  function box(w,h,d,x,y,z,material=wingMat){const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),material);mesh.position.set(x,y,z);plane.add(mesh);return mesh;}
  box(1,.08,3.4,-.15,0,0);box(.65,.08,1.4,-1.15,.07,0);box(.65,.65,.07,-1.12,.35,0);
  const cockpit=new THREE.Mesh(new THREE.SphereGeometry(.25,24,16),cockpitMat);cockpit.scale.set(1.6,.8,.85);cockpit.position.set(.75,.17,0);plane.add(cockpit);
  const axes=[['pitch',new THREE.Vector3(0,0,2.5),0x7cadd8],['roll',new THREE.Vector3(2.5,0,0),0x80b79b],['yaw',new THREE.Vector3(0,2.2,0),0xdfb56b]];
  axes.forEach(([name,end,color])=>scene.add(new THREE.ArrowHelper(end.clone().normalize(),new THREE.Vector3(),end.length(),color,.15,.08)));
  const values={pitch:0,roll:0,yaw:0};let demo=null;
  function apply(){plane.rotation.set(THREE.MathUtils.degToRad(values.roll),THREE.MathUtils.degToRad(values.yaw),THREE.MathUtils.degToRad(values.pitch),'YZX');axes.forEach(([axis])=>{document.getElementById(axis).value=String(values[axis]);document.getElementById(axis+'-value').textContent=Math.round(values[axis])+'°';host.dataset[axis]=String(values[axis]);});}
  document.querySelectorAll('input[type=range]').forEach(input=>input.addEventListener('input',()=>{demo=null;values[input.id]=Number(input.value);apply();}));
  document.querySelectorAll('[data-demo]').forEach(button=>button.addEventListener('click',()=>{const axis=button.dataset.demo;if(matchMedia('(prefers-reduced-motion: reduce)').matches){values[axis]=35;apply();return;}demo={axis,start:performance.now(),from:values[axis],to:35};}));
  document.getElementById('reset').addEventListener('click',()=>{demo=null;values.pitch=values.roll=values.yaw=0;camera.position.set(6,4,7);controls.target.set(0,0,0);controls.update();apply();});
  function resize(){const width=host.clientWidth,height=host.clientHeight;renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();}
  const observer=new ResizeObserver(resize);observer.observe(host);resize();apply();
  renderer.setAnimationLoop(time=>{if(demo){const t=Math.min(1,(time-demo.start)/1500);const eased=t*t*(3-2*t);values[demo.axis]=demo.from+(demo.to-demo.from)*eased;apply();if(t===1)demo=null;}controls.update();scene.updateMatrixWorld();camera.updateMatrixWorld();axes.forEach(([axis,end])=>{const projected=end.clone().project(camera);const label=document.getElementById('label-'+axis);label.style.left=((projected.x+1)*host.clientWidth/2)+'px';label.style.top=((-projected.y+1)*host.clientHeight/2)+'px';});renderer.render(scene,camera);});
  window.aircraft={plane,camera,controls,renderer,values};status.textContent='Drag to orbit · sliders rotate the airplane';status.dataset.ready='true';
  window.addEventListener('pagehide',()=>{renderer.setAnimationLoop(null);observer.disconnect();controls.dispose();scene.traverse(object=>{object.geometry?.dispose();if(object.material){const materials=Array.isArray(object.material)?object.material:[object.material];materials.forEach(material=>material.dispose());}});renderer.dispose();});
 }catch(error){status.textContent='3D scene unavailable: '+error.message;status.dataset.error='true';}
}`,
  jsExpressions: 'setupAircraft();',
};
