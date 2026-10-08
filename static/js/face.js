// Расширенная мимика: прямое управление морфами лица VRoid (брови/глаза/рот/клыки по отдельности),
// аниме-румянец и значки эмоций над головой. Если у модели нет Fcl_-морфов — молча ничего не делает.
import * as THREE from 'three';

// Рецепты эмоций: имя морфа без префикса «Fcl_» → вес. Для моделей не из VRoid есть запасной preset.
export const FACES = {
  neutral:   { m: {}, preset: {} },
  happy:     { m: { BRW_Joy: 0.7, EYE_Fun: 0.45, MTH_Joy: 0.45 }, preset: { happy: 0.6 } },
  laugh:     { m: { BRW_Joy: 0.9, EYE_Joy: 1, MTH_Joy: 0.9 }, preset: { happy: 1 }, emote: 'sparkle' },
  excited:   { m: { BRW_Surprised: 0.6, EYE_Spread: 0.5, MTH_Fun: 0.8 }, preset: { happy: 0.7, surprised: 0.3 }, emote: 'sparkle' },
  smug:      { m: { BRW_Fun: 0.7, EYE_Fun: 0.55, EYE_Close_R: 0.25, MTH_SkinFung_L: 0.8, HA_Fung1: 0.6 }, preset: { relaxed: 0.6 } },
  evil:      { m: { BRW_Angry: 0.75, EYE_Fun: 0.65, MTH_Fun: 0.75, HA_Fung2: 0.9 }, preset: { happy: 0.4, angry: 0.4 } },
  angry:     { m: { BRW_Angry: 1, EYE_Angry: 0.75, MTH_Angry: 0.6, HA_Fung1: 0.4 }, preset: { angry: 0.9 }, emote: 'anger' },
  pout:      { m: { BRW_Angry: 0.55, EYE_Angry: 0.35, MTH_Small: 0.7, MTH_Up: 0.4 }, preset: { angry: 0.4 }, blush: 0.4 },
  sad:       { m: { BRW_Sorrow: 1, EYE_Sorrow: 0.7, MTH_Sorrow: 0.6 }, preset: { sad: 0.8 } },
  cry:       { m: { BRW_Sorrow: 1, EYE_Sorrow: 0.9, MTH_Sorrow: 0.9 }, preset: { sad: 1 }, emote: 'tears' },
  surprised: { m: { BRW_Surprised: 1, EYE_Surprised: 0.85, MTH_Surprised: 0.7 }, preset: { surprised: 0.9 }, emote: 'exclaim' },
  scared:    { m: { BRW_Sorrow: 0.8, EYE_Surprised: 0.7, MTH_Sorrow: 0.5, MTH_Small: 0.4 }, preset: { surprised: 0.5, sad: 0.4 }, emote: 'sweat' },
  shy:       { m: { BRW_Sorrow: 0.45, EYE_Joy: 0.35, MTH_Small: 0.6 }, preset: { relaxed: 0.5 }, blush: 1, emote: 'sweat' },
  love:      { m: { BRW_Joy: 0.5, EYE_Joy: 0.6, MTH_Joy: 0.5 }, preset: { happy: 0.7 }, blush: 0.8, emote: 'heart' },
  think:     { m: { BRW_Surprised: 0.3, EYE_Close_L: 0.3, MTH_Small: 0.45, MTH_Down: 0.2 }, preset: { relaxed: 0.2 }, emote: 'question' },
  confused:  { m: { BRW_Sorrow: 0.5, BRW_Surprised: 0.3, MTH_Down: 0.5, EYE_Close_R: 0.2 }, preset: { sad: 0.3 }, emote: 'question' },
  sleepy:    { m: { EYE_Close: 0.65, BRW_Sorrow: 0.25, MTH_Neutral: 0.5 }, preset: { relaxed: 0.6 }, emote: 'zzz' },
  disgust:   { m: { BRW_Angry: 0.6, EYE_Angry: 0.45, MTH_Down: 0.65, MTH_SkinFung_R: 0.3 }, preset: { angry: 0.4 } },
  relaxed:   { m: { EYE_Fun: 0.4, MTH_Fun: 0.3, BRW_Fun: 0.3 }, preset: { relaxed: 0.7 } },
  proud:     { m: { BRW_Fun: 0.6, EYE_Fun: 0.5, MTH_Fun: 0.5, MTH_SkinFung_L: 0.3 }, preset: { happy: 0.5 }, emote: 'sparkle' },
  bored:     { m: { EYE_Close: 0.4, BRW_Sorrow: 0.2, MTH_Down: 0.4 }, preset: { relaxed: 0.4 } },
  determined:{ m: { BRW_Angry: 0.5, EYE_Spread: 0.2, MTH_Small: 0.3 }, preset: { angry: 0.3 }, emote: 'exclaim' },
  wink:      { m: { EYE_Close_R: 1, BRW_Fun: 0.5, MTH_Fun: 0.6, MTH_SkinFung_R: 0.4 }, preset: { happy: 0.4 } },
};

const EMOTES = {
  anger: { text: '💢', color: '#ff3b5c' }, sweat: { text: '💦', color: '#6cc6ff' }, heart: { text: '❤', color: '#ff5c9a' },
  question: { text: '?', color: '#ffffff' }, exclaim: { text: '!', color: '#ffd23b' }, sparkle: { text: '✨', color: '#ffe680' },
  zzz: { text: 'Zzz', color: '#cfd8ff' }, tears: { text: '💧', color: '#7fc8ff' },
};

function emojiTexture(text, color) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.font = `bold ${text.length > 2 ? 54 : 92}px "Segoe UI Emoji", "Segoe UI", sans-serif`;
  g.lineWidth = 8;
  g.strokeStyle = 'rgba(40,20,60,0.85)';
  g.strokeText(text, 64, 68);
  g.fillStyle = color;
  g.fillText(text, 64, 68);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function blushTexture() {
  const c = document.createElement('canvas');
  c.width = 128;
  c.height = 64;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(64, 32, 2, 64, 32, 60);
  grad.addColorStop(0, 'rgba(255,90,130,0.85)');
  grad.addColorStop(0.6, 'rgba(255,110,150,0.35)');
  grad.addColorStop(1, 'rgba(255,120,160,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 64);
  // аниме-штрихи румянца
  g.strokeStyle = 'rgba(230,60,110,0.55)';
  g.lineWidth = 3;
  for (let i = 0; i < 3; i++) {
    g.beginPath();
    g.moveTo(44 + i * 16, 40);
    g.lineTo(54 + i * 16, 24);
    g.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class Face {
  constructor(vrm, headPos) {
    this.vrm = vrm;
    this.binds = {};      // 'BRW_Joy' -> [[mesh, idx], ...]
    this.cur = {};        // текущие веса морфов
    this.target = {};
    vrm.scene.traverse((o) => {
      if (!o.morphTargetDictionary) return;
      for (const [name, idx] of Object.entries(o.morphTargetDictionary)) {
        const m = name.match(/Fcl_([A-Z]+_[A-Za-z0-9_]+)$/);
        if (m) (this.binds[m[1]] ||= []).push([o, idx]);
      }
    });
    this.rich = Object.keys(this.binds).length > 10;

    // румянец и значки крепим к кости головы
    const head = vrm.humanoid.getNormalizedBoneNode('head');
    const headRaw = vrm.humanoid.getRawBoneNode?.('head') || head;
    this.anchor = new THREE.Group();
    headRaw.add(this.anchor);
    const inv = new THREE.Matrix4().copy(headRaw.matrixWorld).invert();
    // позиция лица в системе кости головы: чуть ниже глаз и вперёд
    this.faceLocal = new THREE.Vector3(0, headPos.y - 0.035, headPos.z + 0.075).applyMatrix4(inv);
    // направление «вперёд» модели в системе головы (у VRM0/VRM1 оно разное)
    const fwd = new THREE.Vector3(0, 0, 1).transformDirection(inv).normalize();
    const side = new THREE.Vector3(1, 0, 0).transformDirection(inv).normalize();

    const bt = blushTexture();
    this.blushMat = new THREE.MeshBasicMaterial({ map: bt, transparent: true, opacity: 0, depthWrite: false,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4 });
    this.blush = [];
    // щёки: под глазами и чуть впереди (если у модели есть кости глаз — берём их)
    const eyes = ['leftEye', 'rightEye'].map((n) => vrm.humanoid.getNormalizedBoneNode(n)).filter(Boolean);
    const cheeks = eyes.length === 2
      ? eyes.map((e) => {
        const w = new THREE.Vector3();
        e.getWorldPosition(w);
        return new THREE.Vector3(w.x * 1.45, w.y - 0.032, w.z + 0.072).applyMatrix4(inv);
      })
      : [-1, 1].map((sg) => this.faceLocal.clone().addScaledVector(side, sg * 0.036));
    for (const c of cheeks) {
      const p = new THREE.Mesh(new THREE.PlaneGeometry(0.04, 0.02), this.blushMat);
      p.position.copy(c);
      p.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), fwd); // плоскость смотрит вперёд лица
      p.renderOrder = 10;
      this.anchor.add(p);
      this.blush.push(p);
    }
    this.blushCur = 0;

    this.emoteSprite = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, depthTest: false, opacity: 0 }));
    this.emoteBase = new THREE.Vector3(0.13, headPos.y + 0.13, headPos.z).applyMatrix4(inv);
    this.emoteUp = new THREE.Vector3(0, 1, 0).transformDirection(inv).normalize();
    this.emoteSprite.position.copy(this.emoteBase);
    this.emoteSprite.renderOrder = 20;
    this.anchor.add(this.emoteSprite);
    this.emoteTex = {};
    this.emoteT = 99;
    this.emoteName = null;
  }

  // показать значок эмоции (anger, sweat, heart, question, exclaim, sparkle, zzz, tears)
  emote(name) {
    const e = EMOTES[name];
    if (!e) return;
    this.emoteTex[name] ||= emojiTexture(e.text, e.color);
    this.emoteSprite.material.map = this.emoteTex[name];
    this.emoteSprite.material.needsUpdate = true;
    this.emoteName = name;
    this.emoteT = 0;
  }

  setEmotion(name) {
    const f = FACES[name] || FACES.neutral;
    this.target = { ...f.m };
    this.blushTarget = f.blush || 0;
    if (f.emote && name !== this.lastEmotion) this.emote(f.emote);
    this.lastEmotion = name;
  }

  presetFor(name) {
    return (FACES[name] || FACES.neutral).preset;
  }

  // до vrm.update(): убираем свой вклад прошлого кадра (часть морфов VRM сам не сбрасывает)
  beforeVrmUpdate() {
    for (const [mesh, idx, v] of this.applied || []) mesh.morphTargetInfluences[idx] -= v;
    this.applied = [];
  }

  // после vrm.update(): дописываем морфы поверх стандартных выражений
  update(dt, t, talking) {
    this.applied ||= [];
    if (this.rich) {
      const k = 1 - Math.exp(-dt * 9);
      // микромимика: брови чуть «играют» при речи, лёгкое движение рта в покое
      const micro = {
        BRW_Surprised: talking ? Math.max(0, Math.sin(t * 3.1) * 0.12) : 0,
        MTH_Fun: !talking ? Math.max(0, Math.sin(t * 0.37) * 0.08) : 0,
      };
      const names = new Set([...Object.keys(this.cur), ...Object.keys(this.target), ...Object.keys(micro)]);
      for (const n of names) {
        let want = (this.target[n] || 0) + (micro[n] || 0);
        // рот эмоции ослабляем во время речи, чтобы не спорил с липсинком
        if (talking && n.startsWith('MTH_')) want *= 0.35;
        const v = (this.cur[n] || 0) + (want - (this.cur[n] || 0)) * k;
        this.cur[n] = v;
        for (const [mesh, idx] of this.binds[n] || []) {
          const add = Math.max(0, Math.min(v, 1 - mesh.morphTargetInfluences[idx]));
          mesh.morphTargetInfluences[idx] += add;
          this.applied.push([mesh, idx, add]);
        }
      }
    }
    // румянец
    this.blushCur += ((this.blushTarget || 0) - this.blushCur) * (1 - Math.exp(-dt * 4));
    this.blushMat.opacity = this.blushCur * 0.6;
    // значок: выскакивает, подпрыгивает и тает
    this.emoteT += dt;
    const e = this.emoteT;
    const life = 2.4;
    const s = e < 0.15 ? e / 0.15 * 1.25 : e < 0.3 ? 1.25 - (e - 0.15) / 0.15 * 0.25 : 1;
    const op = e < life ? (e > life - 0.5 ? (life - e) / 0.5 : 1) : 0;
    const bob = this.emoteName === 'zzz' ? e * 0.03 : Math.abs(Math.sin(e * 5)) * 0.012;
    this.emoteSprite.scale.setScalar(0.085 * s);
    this.emoteSprite.material.opacity = op;
    this.emoteSprite.position.copy(this.emoteBase).addScaledVector(this.emoteUp, bob);
  }
}
