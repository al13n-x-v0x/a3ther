"""
themes.py — 3D website theme presets.

Each theme provides: CSS variables, body background CSS, and a Three.js
scene snippet that gets injected into the generated single-file site.
"""
from __future__ import annotations

THEMES: dict[str, dict] = {
    "neon": {
        "name": "Neon Grid",
        "css": (
            ":root{--bg:#05060f;--fg:#eaffff;--accent:#7df9ff;--accent2:#b44bff;"
            "--panel:rgba(10,18,32,.72);--edge:rgba(125,249,255,.25);}"
        ),
        "background": (
            "background:radial-gradient(900px 500px at 80% -10%,rgba(125,249,255,.12),transparent 60%),"
            "radial-gradient(700px 500px at 10% 110%,rgba(180,75,255,.12),transparent 60%),#05060f;"
        ),
        "scene": (
            "// NEON GRID\n"
            "const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,innerWidth/innerHeight,.1,1000),"
            "renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});\n"
            "renderer.setSize(innerWidth,innerHeight);document.getElementById('bg').appendChild(renderer.domElement);\n"
            "const grid=new THREE.GridHelper(40,40,0x7df9ff,0x7df9ff);grid.material.opacity=.35;grid.material.transparent=true;scene.add(grid);\n"
            "const geo=new THREE.IcosahedronGeometry(2,1),mat=new THREE.MeshStandardMaterial({color:0x7df9ff,wireframe:true,transparent:true,opacity:.5}),core=new THREE.Mesh(geo,mat);scene.add(core);\n"
            "scene.add(new THREE.PointLight(0x7df9ff,2,30));camera.position.set(0,6,10);camera.lookAt(0,0,0);\n"
            "function tick(){requestAnimationFrame(tick);core.rotation.x+=.003;core.rotation.y+=.005;grid.position.y=-2;renderer.render(scene,camera)}tick();"
        ),
    },
    "glass": {
        "name": "Glassmorphism",
        "css": (
            ":root{--bg:#0a0f1c;--fg:#eef4ff;--accent:#6ea8ff;--accent2:#5ef7c0;"
            "--panel:rgba(255,255,255,.08);--edge:rgba(255,255,255,.18);}"
        ),
        "background": (
            "background:linear-gradient(160deg,#0a0f1c 0%,#14203a 50%,#0a0f1c 100%);"
        ),
        "scene": (
            "// GLASS ORBS\n"
            "const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,innerWidth/innerHeight,.1,1000),"
            "renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});\n"
            "renderer.setSize(innerWidth,innerHeight);document.getElementById('bg').appendChild(renderer.domElement);\n"
            "const orbs=[];for(let i=0;i<8;i++){const m=new THREE.Mesh(new THREE.SphereGeometry(.5+Math.random()*.7,24,24),"
            "new THREE.MeshPhysicalMaterial({color:new THREE.Color().setHSL(i/8,.8,.6),transmission:.85,roughness:.1}));"
            "m.position.set((Math.random()-.5)*18,(Math.random()-.5)*8,(Math.random()-.5)*8);scene.add(m);orbs.push(m)}\n"
            "camera.position.z=8;function tick(){requestAnimationFrame(tick);orbs.forEach((o,i)=>{o.position.y+=Math.sin(Date.now()*.001+i)*.004;o.rotation.y+=.003});renderer.render(scene,camera)}tick();"
        ),
    },
    "hologram": {
        "name": "Hologram",
        "css": (
            ":root{--bg:#020a06;--fg:#c8ffe3;--accent:#34f5a2;--accent2:#00d4ff;"
            "--panel:rgba(0,30,20,.6);--edge:rgba(52,245,162,.3);}"
        ),
        "background": (
            "background:radial-gradient(800px 400px at 50% 0%,rgba(52,245,162,.1),transparent 60%),#020a06;"
        ),
        "scene": (
            "// HOLOGRAM CORE\n"
            "const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,innerWidth/innerHeight,.1,1000),"
            "renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});\n"
            "renderer.setSize(innerWidth,innerHeight);document.getElementById('bg').appendChild(renderer.domElement);\n"
            "const core=new THREE.Mesh(new THREE.TorusKnotGeometry(1.4,.4,120,16),"
            "new THREE.MeshStandardMaterial({color:0x34f5a2,wireframe:true,transparent:true,opacity:.6}));scene.add(core);\n"
            "const ring=new THREE.Mesh(new THREE.TorusGeometry(3,.03,16,64),new THREE.MeshBasicMaterial({color:0x00d4ff,transparent:true,opacity:.5}));scene.add(ring);\n"
            "scene.add(new THREE.PointLight(0x34f5a2,2,20));camera.position.z=6;let a=0;\n"
            "function tick(){requestAnimationFrame(tick);a+=.005;core.rotation.x=a;core.rotation.y=a*1.3;ring.rotation.z=a*.7;renderer.render(scene,camera)}tick();"
        ),
    },
}

DEFAULT_THEME = "neon"
