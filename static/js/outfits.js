// Наряды: перекраска текстур одежды/волос (через canvas-фильтры — детали сохраняются) + 3D-аксессуары на костях.
import * as THREE from 'three';

// filter — CSS-фильтр для текстуры; parts — какие материалы красить (по началу имени у VRoid)
export const OUTFITS = {
  'Обычная': { paint: {}, acc: [] },
  'Неко': {
    paint: { Tops: '#ffb3d9', Bottoms: 'invert(0.88) sepia(1) hue-rotate(290deg) saturate(2.2)' },
    acc: ['catEars', 'catTail', 'bell'],
  },
  'Ведьмочка': {
    paint: { Tops: '#3b2160', Bottoms: 'invert(0.3) sepia(1) hue-rotate(235deg) saturate(3) brightness(0.8)',
      Shoes: 'brightness(0.6)' },
    acc: ['witchHat'],
  },
  'Ангел': {
    paint: { Tops: 'sepia(0.15) brightness(1.05)', Bottoms: 'invert(0.95)', Shoes: 'invert(0.9)', Hair: 'brightness(1.75) saturate(0.5) sepia(0.45)' },
    acc: ['halo', 'wings'],
  },
  'Демонесса': {
    paint: { Tops: '#3a0d16', Bottoms: 'invert(0.25) sepia(1) hue-rotate(320deg) saturate(4) brightness(0.7)',
      Hair: 'brightness(0.6) sepia(1) hue-rotate(310deg) saturate(3)' },
    acc: ['horns', 'demonTail'],
  },
  'Киберпанк': {
    paint: { Tops: '#123a46', Bottoms: 'invert(0.35) sepia(1) hue-rotate(160deg) saturate(4)',
      Hair: 'brightness(1.6) sepia(1) hue-rotate(275deg) saturate(3)' },
    acc: ['headphones'],
  },
  'Школьница': {
    paint: { Tops: 'sepia(0.1) brightness(1.03)', Bottoms: 'invert(0.3) sepia(1) hue-rotate(185deg) saturate(3) brightness(0.75)' },
    acc: ['bow'],
  },
  'Зимняя': {
    paint: { Tops: '#d8323f', Bottoms: 'invert(0.95)' },
    acc: ['santaHat'],
  },
};

// ---------- перекраска ----------
const _orig = new WeakMap();

function recolor(tex, filter) {
  const img = tex.image;
  if (!img || !img.width) return tex;
  const c = document.createElement('canvas');
  c.width = img.width;
  c.height = img.height;
  const g = c.getContext('2d');
  g.filter = filter;
  g.drawImage(img, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.flipY = tex.flipY;
  t.colorSpace = tex.colorSpace;
  t.wrapS = tex.wrapS;
  t.wrapT = tex.wrapT;
  t.needsUpdate = true;
  return t;
}

function paint(vrm, rules) {
  vrm.scene.traverse((o) => {
    if (!o.isMesh) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (!m.map) continue;
      if (!_orig.has(m)) {
        _orig.set(m, { map: m.map, color: m.color?.clone(), shade: m.shadeColorFactor?.clone(), shadeTex: m.shadeMultiplyTexture });
      }
      const orig = _orig.get(m);
      const part = Object.keys(rules).find((p) => m.name.startsWith(p));
      const rule = part ? rules[part] : null;
      // при смене наряда сначала возвращаем родное
      if (orig.color) m.color.copy(orig.color);
      if (orig.shade) m.shadeColorFactor.copy(orig.shade);
      if (rule && rule.startsWith('#')) {
        // белые вещи красим умножением цвета материала (фильтр по белому почти не виден)
        m.map = orig.map;
        m.color?.set(rule);
        m.shadeColorFactor?.set(rule).multiplyScalar(0.72);
      } else {
        m.map = rule ? recolor(orig.map, rule) : orig.map;
      }
      if (m.uniforms?.map) m.uniforms.map.value = m.map;
      if (m.shadeMultiplyTexture !== undefined) {
        m.shadeMultiplyTexture = rule && !rule.startsWith('#') ? m.map : orig.shadeTex;
      }
      m.needsUpdate = true;
    }
  });
}

// ---------- аксессуары ----------
const mat = (color, opts = {}) => new THREE.MeshToonMaterial({ color, ...opts });

function canvasTex(draw, w = 256, h = 256) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const BUILDERS = {
  catEars(f) {
    const g = new THREE.Group();
    for (const s of [-1, 1]) {
      const ear = new THREE.Mesh(new THREE.ConeGeometry(0.045, 0.095, 4), mat(0x5a3a2a));
      const inner = new THREE.Mesh(new THREE.ConeGeometry(0.028, 0.06, 4), mat(0xffa7c4));
      inner.position.set(0, -0.005, 0.012);
      ear.add(inner);
      ear.position.copy(f.top).addScaledVector(f.side, s * 0.075).addScaledVector(f.up, 0.01);
      ear.quaternion.copy(f.headQ).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, Math.PI / 4, -s * 0.35)));
      g.add(ear);
    }
    return { bone: 'head', obj: g };
  },
  catTail(f) {
    const pts = [];
    for (let i = 0; i <= 12; i++) {
      const t = i / 12;
      pts.push(new THREE.Vector3(Math.sin(t * 3) * 0.05, -0.05 + t * 0.32 - t * t * 0.12, -0.09 - t * 0.22));
    }
    const curve = new THREE.CatmullRomCurve3(pts);
    const tail = new THREE.Mesh(new THREE.TubeGeometry(curve, 30, 0.018, 8), mat(0x5a3a2a));
    const g = new THREE.Group();
    const pivot = new THREE.Group();
    pivot.add(tail);
    g.add(pivot);
    g.position.copy(f.hips);
    g.userData.sway = (t) => { pivot.rotation.z = Math.sin(t * 2.2) * 0.35; pivot.rotation.x = Math.sin(t * 1.3) * 0.12; };
    return { bone: 'hips', obj: g, world: true };
  },
  bell(f) {
    const b = new THREE.Mesh(new THREE.SphereGeometry(0.014, 16, 12), mat(0xffd34d, { emissive: 0x332200 }));
    b.position.copy(f.neck).addScaledVector(f.fwd, 0.05).addScaledVector(f.up, -0.04);
    return { bone: 'neck', obj: b };
  },
  witchHat(f) {
    const g = new THREE.Group();
    const brim = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.008, 40), mat(0x241238));
    const cone = new THREE.Mesh(new THREE.ConeGeometry(0.085, 0.24, 32), mat(0x2d1648));
    cone.position.y = 0.12;
    cone.rotation.z = -0.25;
    const band = new THREE.Mesh(new THREE.CylinderGeometry(0.087, 0.09, 0.025, 32, 1, true), mat(0xc77dff));
    band.position.y = 0.02;
    g.add(brim, cone, band);
    g.position.copy(f.top).addScaledVector(f.up, -0.025);
    g.quaternion.copy(f.headQ).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(-0.15, 0, 0.12)));
    return { bone: 'head', obj: g };
  },
  halo(f) {
    const h = new THREE.Mesh(new THREE.TorusGeometry(0.075, 0.009, 12, 48),
      new THREE.MeshBasicMaterial({ color: 0xfff1a8 }));
    h.position.copy(f.top).addScaledVector(f.up, 0.07);
    h.quaternion.copy(f.headQ).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2 - 0.25, 0, 0)));
    return { bone: 'head', obj: h };
  },
  wings(f) {
    const tex = canvasTex((g, w, h) => {
      g.fillStyle = 'rgba(255,255,255,0.95)';
      for (let i = 0; i < 6; i++) {
        g.beginPath();
        g.ellipse(w * 0.25 + i * 22, h * 0.35 + i * 18, 26, 70 - i * 6, -0.7 + i * 0.12, 0, Math.PI * 2);
        g.fill();
      }
      g.strokeStyle = 'rgba(200,210,255,0.8)';
      g.lineWidth = 3;
      g.stroke();
    });
    const g = new THREE.Group();
    for (const s of [-1, 1]) {
      const wing = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.42),
        new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide, depthWrite: false }));
      wing.position.set(s * 0.2, 0.05, -0.12);
      wing.scale.x = s;
      wing.rotation.y = s * 0.5;
      g.add(wing);
    }
    g.position.copy(f.chest);
    g.userData.sway = (t) => { g.children.forEach((w, i) => { w.rotation.y = (i ? 1 : -1) * (0.5 + Math.sin(t * 2.5) * 0.12); }); };
    return { bone: 'chest', obj: g, world: true };
  },
  horns(f) {
    const g = new THREE.Group();
    for (const s of [-1, 1]) {
      const horn = new THREE.Mesh(new THREE.ConeGeometry(0.018, 0.08, 16), mat(0x2b0a10, { emissive: 0x220000 }));
      horn.position.copy(f.top).addScaledVector(f.side, s * 0.045).addScaledVector(f.fwd, 0.02).addScaledVector(f.up, 0.005);
      horn.quaternion.copy(f.headQ).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(-0.35, 0, -s * 0.45)));
      g.add(horn);
    }
    return { bone: 'head', obj: g };
  },
  demonTail(f) {
    const pts = [];
    for (let i = 0; i <= 12; i++) {
      const t = i / 12;
      pts.push(new THREE.Vector3(Math.sin(t * 4) * 0.06, -0.08 + t * 0.15, -0.08 - t * 0.3));
    }
    const tail = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 30, 0.011, 8), mat(0x1a0508));
    const tip = new THREE.Mesh(new THREE.ConeGeometry(0.03, 0.06, 3), mat(0xa0101e));
    tip.position.copy(pts[12]);
    tip.rotation.x = -Math.PI / 2;
    const g = new THREE.Group();
    const pivot = new THREE.Group();
    pivot.add(tail, tip);
    g.add(pivot);
    g.position.copy(f.hips);
    g.userData.sway = (t) => { pivot.rotation.z = Math.sin(t * 1.7) * 0.3; };
    return { bone: 'hips', obj: g, world: true };
  },
  headphones(f) {
    const g = new THREE.Group();
    const band = new THREE.Mesh(new THREE.TorusGeometry(0.098, 0.012, 10, 40, Math.PI), mat(0x15151f));
    band.position.copy(f.ears).addScaledVector(f.up, 0.0);
    band.quaternion.copy(f.headQ);
    g.add(band);
    for (const s of [-1, 1]) {
      const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.038, 0.038, 0.03, 24), mat(0x15151f));
      cup.position.copy(f.ears).addScaledVector(f.side, s * 0.098);
      cup.quaternion.copy(f.headQ).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, Math.PI / 2)));
      const glow = new THREE.Mesh(new THREE.TorusGeometry(0.03, 0.004, 8, 32), new THREE.MeshBasicMaterial({ color: 0x00f0ff }));
      glow.position.y = s * 0.016;
      glow.rotation.x = Math.PI / 2;
      cup.add(glow);
      g.add(cup);
    }
    return { bone: 'head', obj: g };
  },
  bow(f) {
    const g = new THREE.Group();
    const red = mat(0xe8304f);
    for (const s of [-1, 1]) {
      const loop = new THREE.Mesh(new THREE.ConeGeometry(0.03, 0.06, 4), red);
      loop.rotation.z = s * Math.PI / 2;
      loop.position.x = s * 0.03;
      g.add(loop);
    }
    g.add(new THREE.Mesh(new THREE.SphereGeometry(0.014, 12, 8), red));
    g.position.copy(f.top).addScaledVector(f.side, 0.07).addScaledVector(f.up, -0.02);
    g.quaternion.copy(f.headQ).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, -0.4)));
    return { bone: 'head', obj: g };
  },
  santaHat(f) {
    const g = new THREE.Group();
    const cone = new THREE.Mesh(new THREE.ConeGeometry(0.1, 0.2, 32), mat(0xd01c28));
    cone.position.y = 0.1;
    cone.rotation.z = 0.5;
    const rim = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.022, 12, 40), mat(0xffffff));
    rim.rotation.x = Math.PI / 2;
    const pom = new THREE.Mesh(new THREE.SphereGeometry(0.03, 16, 12), mat(0xffffff));
    pom.position.set(0.1, 0.17, 0);
    g.add(cone, rim, pom);
    g.position.copy(f.top).addScaledVector(f.up, -0.035);
    g.quaternion.copy(f.headQ).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(-0.15, 0, 0)));
    return { bone: 'head', obj: g };
  },
};

export class Wardrobe {
  constructor(vrm) {
    this.vrm = vrm;
    this.items = [];
  }

  // ориентиры на модели в мировых координатах (поза по умолчанию)
  frame() {
    const h = this.vrm.humanoid;
    const wp = (n) => { const v = new THREE.Vector3(); h.getNormalizedBoneNode(n)?.getWorldPosition(v); return v; };
    const head = wp('head');
    const eyeL = wp('leftEye');
    const eyeR = wp('rightEye');
    const eyes = eyeL.clone().add(eyeR).multiplyScalar(0.5);
    const top = eyes.clone();
    top.y = head.y + (eyes.y - head.y) * 2.6 + 0.02;
    // у аниме-причёсок макушка заметно выше черепа — берём верх меша волос
    this.vrm.scene.traverse((o) => {
      const m = Array.isArray(o.material) ? o.material[0] : o.material;
      if (o.isMesh && m?.name?.startsWith('Hair')) {
        o.geometry.computeBoundingBox();
        top.y = Math.max(top.y, o.geometry.boundingBox.max.y - 0.012);
      }
    });
    return {
      top, eyes, ears: new THREE.Vector3(head.x, eyes.y + 0.01, head.z), head,
      neck: wp('neck'), chest: wp('upperChest').lengthSq() ? wp('upperChest') : wp('chest'), hips: wp('hips'),
      up: new THREE.Vector3(0, 1, 0), side: new THREE.Vector3(1, 0, 0), fwd: new THREE.Vector3(0, 0, 1),
      headQ: new THREE.Quaternion(),
    };
  }

  wear(name) {
    for (const it of this.items) it.obj.parent?.remove(it.obj);
    this.items = [];
    const o = OUTFITS[name] || OUTFITS['Обычная'];
    paint(this.vrm, o.paint);
    this.vrm.scene.updateMatrixWorld(true);
    const f = this.frame();
    for (const key of o.acc) {
      const it = BUILDERS[key]?.(f);
      if (!it) continue;
      const bone = this.vrm.humanoid.getRawBoneNode?.(it.bone) || this.vrm.humanoid.getNormalizedBoneNode(it.bone);
      if (!bone) continue;
      // переводим мировые координаты в систему кости, чтобы аксессуар двигался вместе с ней
      bone.updateMatrixWorld(true);
      const inv = new THREE.Matrix4().copy(bone.matrixWorld).invert();
      const wq = it.obj.quaternion.clone();
      it.obj.position.applyMatrix4(inv);
      const bq = new THREE.Quaternion();
      bone.getWorldQuaternion(bq);
      it.obj.quaternion.copy(bq.invert().multiply(wq));
      bone.add(it.obj);
      this.items.push(it);
    }
    this.name = name;
  }

  update(t) {
    for (const it of this.items) it.obj.userData.sway?.(t);
  }
}
