// 3D-аватар VRM: поза, дыхание, моргание, взгляд, эмоции и липсинк.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { GESTURES, EMOTION_POSE, REST, talkBeat } from './gestures.js';
import { Face, FACES } from './face.js';
import { Wardrobe, OUTFITS } from './outfits.js';

// эмоция -> веса выражений VRM + поза головы
const EMOTIONS = {
  neutral:   { expr: {},                              head: [0, 0, 0] },
  happy:     { expr: { happy: 0.85 },                 head: [-0.05, 0, 0.08] },
  sad:       { expr: { sad: 0.8 },                    head: [0.18, 0, -0.05] },
  angry:     { expr: { angry: 0.85 },                 head: [0.06, 0, 0] },
  surprised: { expr: { surprised: 0.9 },              head: [-0.1, 0, 0] },
  relaxed:   { expr: { relaxed: 0.75 },               head: [0, 0, 0.1] },
  shy:       { expr: { happy: 0.35, relaxed: 0.4 },   head: [0.15, 0.12, 0.12] },
  think:     { expr: { relaxed: 0.25 },               head: [-0.12, -0.15, 0.14] },
  laugh:     { expr: {}, head: [-0.12, 0, 0.06] },
  excited:   { expr: {}, head: [-0.08, 0, 0.05] },
  smug:      { expr: {}, head: [-0.06, 0.1, -0.1] },
  evil:      { expr: {}, head: [0.1, 0, 0.05] },
  pout:      { expr: {}, head: [0.05, 0.25, -0.05] },
  cry:       { expr: {}, head: [0.2, 0, -0.08] },
  scared:    { expr: {}, head: [0.08, 0, 0] },
  love:      { expr: {}, head: [0.04, 0, 0.15] },
  confused:  { expr: {}, head: [0, 0.05, 0.2] },
  sleepy:    { expr: {}, head: [0.15, 0, 0.12] },
  disgust:   { expr: {}, head: [0.05, -0.15, 0] },
  wink:      { expr: {}, head: [-0.03, 0, 0.12] },
  proud:     { expr: {}, head: [-0.15, 0, 0.05] },
  bored:     { expr: {}, head: [0.1, 0.1, 0.15] },
  determined:{ expr: {}, head: [0.05, 0, 0] },
};
const EXPR_KEYS = ['happy', 'sad', 'angry', 'surprised', 'relaxed'];
const VOWELS = ['aa', 'ih', 'ou', 'ee', 'oh'];

export class Avatar {
  constructor(canvas, { mascot = false } = {}) {
    this.mascot = mascot;
    // в режиме фигурки фон прозрачный, а буфер сохраняем, чтобы проверять пиксель под мышкой
    this.renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: mascot, premultipliedAlpha: true, preserveDrawingBuffer: mascot,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    if (mascot) this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.1, 50);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    if (mascot) this.controls.enabled = false;

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(1, 2, 2);
    const rim = new THREE.DirectionalLight(0xff9ad5, 1.2);
    rim.position.set(-2, 1.5, -1.5);
    this.scene.add(key, rim, new THREE.AmbientLight(0xffffff, 0.7));

    this.lookTarget = new THREE.Object3D();
    this.scene.add(this.lookTarget);
    this.mouse = new THREE.Vector2();
    this.mousePx = null;
    this.overModel = false;
    this.onHoverChange = null;
    this.mouthSource = null; // () => [level, vowel] — точный липсинк от аудио
    window.addEventListener('pointermove', (e) => {
      this.mouse.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
      this.mousePx = [e.clientX, e.clientY];
    });
    window.addEventListener('pointerleave', () => { this.mousePx = null; });

    this.vrm = null;
    this.clock = new THREE.Clock();
    this.t = 0;
    this.emotion = 'neutral';
    this.emotionUntil = 0;
    this.exprCur = {};
    this.mouth = 0;
    this.vowelMix = { aa: 1, ih: 0, ou: 0, ee: 0, oh: 0 };
    this.mouthCur = {};
    this.nextBlink = 2;
    this.blinkT = -1;
    this.headCur = [0, 0, 0];
    this.speaking = false;
    this.listening = false;
    this.distance = 1;
    this.headY = 1.35;
    this.gestureName = null;
    this.gestureUntil = 0;
    this.armCur = {};
    this.beatSeed = 1;
    this.nextBeat = 0;
    this.dt = 0.016;

    window.addEventListener('resize', () => this.resize());
    this.resize();
    this.renderer.setAnimationLoop(() => this.update());
  }

  setBackground(color) { if (!this.mascot) this.scene.background = new THREE.Color(color); }

  setDistance(d) { this.distance = d; this.frame(); }

  resize() {
    this.renderer.setSize(innerWidth, innerHeight, false);
    this.camera.aspect = innerWidth / innerHeight;
    this.camera.updateProjectionMatrix();
  }

  frame() {
    const y = this.headY;
    if (this.mascot) {
      // в полный рост: при distance < 1 приближаемся к лицу
      const d = this.distance;
      // над головой оставляем место для облачка с репликой
      const ty = THREE.MathUtils.lerp(y - 0.05, y * 0.53 + 0.2, Math.min(1, d));
      this.controls.target.set(0, ty, 0);
      this.camera.position.set(0, ty + 0.05, 4.3 * d + 0.7 * (1 - Math.min(1, d)));
    } else {
      this.controls.target.set(0, y - 0.12, 0);
      this.camera.position.set(0, y - 0.02, 1.7 * this.distance);
    }
    this.controls.update();
  }

  // непрозрачен ли пиксель под курсором (для «кликов насквозь» в режиме фигурки)
  hitTest() {
    if (!this.mousePx) return false;
    const gl = this.renderer.getContext();
    const pr = this.renderer.getPixelRatio();
    const x = Math.floor(this.mousePx[0] * pr);
    const y = Math.floor((innerHeight - this.mousePx[1]) * pr);
    const px = new Uint8Array(4);
    gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    return px[3] > 20;
  }

  async load(url) {
    const loader = new GLTFLoader();
    loader.register((p) => new VRMLoaderPlugin(p));
    const gltf = await loader.loadAsync(url);
    const vrm = gltf.userData.vrm;
    if (!vrm) throw new Error('Файл не является VRM');
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    if (VRMUtils.combineSkeletons) VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm);
    vrm.scene.traverse((o) => { o.frustumCulled = false; });

    if (this.vrm) {
      this.scene.remove(this.vrm.scene);
      VRMUtils.deepDispose(this.vrm.scene);
    }
    this.vrm = vrm;
    this.scene.add(vrm.scene);
    if (vrm.lookAt) vrm.lookAt.target = this.lookTarget;

    this.hipsY = vrm.humanoid.getNormalizedBoneNode('hips')?.position.y || 0;
    this.applyPose(0);
    vrm.update(0);
    this.wardrobe = new Wardrobe(vrm);
    this.wardrobe.wear(this.outfit || 'Обычная');
    const head = vrm.humanoid.getNormalizedBoneNode('head');
    const p = new THREE.Vector3();
    head.getWorldPosition(p);
    this.headY = p.y + 0.05;
    this.exprNames = new Set(Object.keys(vrm.expressionManager?.expressionMap || {}));
    this.face = new Face(vrm, p);
    this.face.setEmotion(this.emotion);
    this.frame();
  }

  hasExpr(name) { return this.exprNames?.has(name); }

  setExpr(name, v) {
    if (this.hasExpr(name)) this.vrm.expressionManager.setValue(name, v);
  }

  // эмоция держится holdSec секунд, потом плавно уходит в нейтраль
  setEmotion(name, holdSec = 6) {
    if (!EMOTIONS[name] && !FACES[name]) return;
    this.emotion = name;
    this.emotionUntil = this.t + holdSec;
    this.face?.setEmotion(name);
  }

  emote(name) { this.face?.emote(name); }

  setOutfit(name) {
    this.outfit = name;
    this.wardrobe?.wear(name);
  }

  // сама что-то делает, когда скучает
  idleAction() {
    if (this.speaking || this.listening || (this.gestureName && this.t < this.gestureUntil)) return;
    const pick = this.emotion === 'sleepy' ? 'yawn' : ['stretch', 'lookaround', 'hairtouch', 'lookaround'][Math.floor(Math.random() * 4)];
    this.gesture(pick);
  }

  // level 0..1, vowel — какой гласный звук доминирует
  setMouth(level, vowel = 'aa') {
    this.mouth = level;
    for (const v of VOWELS) this.vowelMix[v] = v === vowel ? 1 : 0;
  }

  bone(name) { return this.vrm.humanoid.getNormalizedBoneNode(name); }

  // жест из библиотеки (wave, cheer, hips, shrug, point, facepalm, heart)
  gesture(name) {
    const g = GESTURES[name];
    if (!g) return;
    this.gestureName = name;
    this.gestureUntil = this.t + (g.duration || 2.5);
  }

  // целевая поза рук на этот кадр
  armTargets(t) {
    let g = null;
    let explicit = false;
    if (this.gestureName && t < this.gestureUntil) {
      g = GESTURES[this.gestureName];
      explicit = true;
    } else if (this.emotion !== 'neutral' && EMOTION_POSE[this.emotion]) {
      g = GESTURES[EMOTION_POSE[this.emotion]];
    }
    if (this.speaking && t > this.nextBeat) {
      this.beatSeed = Math.random() * 100;
      this.nextBeat = t + 1.2 + Math.random() * 1.3;
    }
    const beat = this.speaking && !g ? talkBeat(t, this.beatSeed, 0.6 + this.mouth * 0.4) : null;
    const sway = Math.sin(t * 1.1) * 0.03;
    const out = {};
    for (const side of ['L', 'R']) {
      const base = g ? g[side] : REST;
      const dyn = (g && g.dyn && g.dyn(t, side)) || {};
      const bt = (beat && beat[side]) || {};
      for (const part of ['upper', 'lower', 'hand']) {
        const v = [...base[part]];
        for (const add of [dyn[part], bt[part]]) if (add) for (let i = 0; i < 3; i++) v[i] += add[i];
        if (part === 'upper' && !explicit) v[2] += sway;
        // зеркалим для правой руки
        out[side + part] = side === 'L' ? v : [v[0], -v[1], -v[2]];
      }
    }
    out.shoulders = (g && g.shoulders) || 0;
    out.head = (g && g.head) || [0, 0, 0];
    out.body = (g && g.body && g.body(t)) || null;
    return out;
  }

  // реакция на «тык» мышкой: вздрагивает
  poke() {
    this.bounce = 1;
    this.setEmotion('surprised', 1.5);
  }

  applyPose(t) {
    const b = (n) => this.bone(n);
    const breath = Math.sin(t * 1.7);
    const sway = Math.sin(t * 0.6);
    const talk = this.speaking ? this.mouth : 0;

    const set = (n, x, y, z) => { const o = b(n); if (o) o.rotation.set(x, y, z); };
    this.bounce = Math.max(0, (this.bounce || 0) - 0.04);
    const jolt = Math.sin(this.bounce * Math.PI * 3) * this.bounce * 0.12;
    // поза «с весом на одной ноге»: бедро в сторону, колено согнуто, руки мягко у тела
    set('hips', 0, sway * 0.03 + 0.06, 0.04 + sway * 0.015);
    set('spine', breath * 0.015 + 0.02 - jolt, -sway * 0.02 - 0.03, -0.03);
    set('chest', breath * 0.02, 0, -0.02 - sway * 0.01);
    set('upperChest', breath * 0.01, 0, 0);
    set('leftUpperLeg', 0.02, 0, -0.04);
    set('rightUpperLeg', -0.12, 0, -0.02);
    set('rightLowerLeg', 0.22, 0, 0);
    set('rightFoot', -0.08, 0, 0);
    set('leftLowerLeg', 0.03, 0, 0);

    // руки: плавно тянемся к целевой позе жеста/эмоции/речи
    const tg = this.armTargets(t);
    const k = 1 - Math.exp(-this.dt * 7);
    const BONES = { Lupper: 'leftUpperArm', Llower: 'leftLowerArm', Lhand: 'leftHand',
      Rupper: 'rightUpperArm', Rlower: 'rightLowerArm', Rhand: 'rightHand' };
    for (const [key, bone] of Object.entries(BONES)) {
      const cur = this.armCur[key] || (this.armCur[key] = [...tg[key]]);
      for (let i = 0; i < 3; i++) cur[i] += (tg[key][i] - cur[i]) * k;
      set(bone, cur[0], cur[1], cur[2]);
    }
    this.shCur = (this.shCur || 0) + (tg.shoulders - (this.shCur || 0)) * k;
    set('leftShoulder', 0, 0, 0.05 + this.shCur);
    set('rightShoulder', 0, 0, -0.05 - this.shCur);
    this.gHead = (this.gHead || [0, 0, 0]).map((v, i) => v + (tg.head[i] - v) * k);
    // движения всем телом (танец, поклон, кивок...) — плавно добавляем к позе
    const bt = tg.body || {};
    this.bodyCur ||= {};
    for (const key of ['hips', 'spine', 'chest', 'neck', 'head']) {
      const want = bt[key] || [0, 0, 0];
      const cur = this.bodyCur[key] ||= [0, 0, 0];
      for (let i = 0; i < 3; i++) cur[i] += (want[i] - cur[i]) * k;
    }
    this.bounceCur = (this.bounceCur || 0) + ((bt.bounce || 0) - (this.bounceCur || 0)) * Math.min(1, k * 2.5);
    const hipsBone = b('hips');
    if (hipsBone && this.hipsY !== undefined) hipsBone.position.y = this.hipsY + this.bounceCur;
    for (const side of ['left', 'right']) {
      for (const f of ['Index', 'Middle', 'Ring', 'Little']) {
        for (const seg of ['Proximal', 'Intermediate']) {
          set(`${side}${f}${seg}`, 0, 0, side === 'left' ? -0.25 : 0.25);
        }
      }
    }

    // голова: эмоция + покачивание + кивки при речи
    const target = (EMOTIONS[this.emotion] || EMOTIONS.neutral).head;
    for (let i = 0; i < 3; i++) this.headCur[i] += (target[i] - this.headCur[i]) * 0.06;
    const nod = talk * 0.06 * Math.sin(t * 9);
    const listenTilt = this.listening ? 0.06 : 0;
    set('neck', this.headCur[0] * 0.4, this.headCur[1] * 0.4 + sway * 0.03, this.headCur[2] * 0.4);
    set('head', this.headCur[0] * 0.6 + nod + this.gHead[0], this.headCur[1] * 0.6 + Math.sin(t * 0.45) * 0.06,
      this.headCur[2] * 0.6 + listenTilt + Math.sin(t * 0.33) * 0.03);
    for (const key of ['hips', 'spine', 'chest', 'neck', 'head']) {
      const o = b(key);
      const d = this.bodyCur?.[key];
      if (o && d) { o.rotation.x += d[0]; o.rotation.y += d[1]; o.rotation.z += d[2]; }
    }
  }

  updateExpressions(dt) {
    if (this.emotion !== 'neutral' && this.t > this.emotionUntil && !this.speaking) this.setEmotion('neutral');
    // у VRoid-моделей эмоции рисуются отдельными морфами (face.js), стандартные выражения не нужны
    const target = this.face?.rich ? {} : (this.face ? this.face.presetFor(this.emotion) : (EMOTIONS[this.emotion] || EMOTIONS.neutral).expr);
    const k = 1 - Math.exp(-dt * 10);
    for (const name of EXPR_KEYS) {
      const cur = this.exprCur[name] || 0;
      const v = cur + ((target[name] || 0) - cur) * k;
      this.exprCur[name] = v;
      this.setExpr(name, v);
    }

    // рот: при эмоциях «happy» рот уже открыт, поэтому ослабляем гласные
    // рот открывается быстрее, чем закрывается — так речь выглядит чётче
    const damp = 1 - (this.exprCur.happy || 0) * 0.4;
    for (const v of VOWELS) {
      const want = this.mouth * this.vowelMix[v] * damp;
      const cur = this.mouthCur[v] || 0;
      const nv = cur + (want - cur) * (1 - Math.exp(-dt * (want > cur ? 35 : 16)));
      this.mouthCur[v] = nv;
      this.setExpr(v, nv);
    }

    // моргание
    if (this.blinkT < 0 && this.t > this.nextBlink) this.blinkT = 0;
    let blink = 0;
    if (this.blinkT >= 0) {
      this.blinkT += dt;
      const d = 0.16;
      blink = this.blinkT < d / 2 ? this.blinkT / (d / 2) : 1 - (this.blinkT - d / 2) / (d / 2);
      if (this.blinkT >= d) {
        this.blinkT = -1;
        blink = 0;
        this.nextBlink = this.t + 1.5 + Math.random() * 4.5;
        if (Math.random() < 0.15) this.nextBlink = this.t + 0.25; // двойное моргание
      }
    }
    // при сильных эмоциях с закрытыми глазами (happy/relaxed) не моргаем поверх
    const fc = this.face?.cur || {};
    const eyesBusy = Math.max(this.exprCur.happy || 0, this.exprCur.relaxed || 0, fc.EYE_Joy || 0, fc.EYE_Close || 0,
      fc.EYE_Close_R || 0, fc.EYE_Close_L || 0);
    this.setExpr('blink', Math.max(0, Math.min(1, blink)) * (1 - eyesBusy));
  }

  update() {
    const dt = Math.min(this.clock.getDelta(), 0.1);
    this.t += dt;
    this.dt = dt;
    this.controls.update();
    if (this.vrm) {
      // смотрит в камеру, немного следя за мышкой
      // взгляд: в камеру/на мышку + быстрые саккады; когда думает — вверх-вбок
      if (this.t > (this.nextSaccade || 0)) {
        this.saccade = [(Math.random() - 0.5) * 0.25, (Math.random() - 0.5) * 0.15];
        this.nextSaccade = this.t + 0.4 + Math.random() * 2.2;
      }
      const sc = this.saccade || [0, 0];
      const thinking = this.emotion === 'think' || this.emotion === 'confused';
      const cam = this.camera.position;
      this.lookTarget.position.set(cam.x + this.mouse.x * 0.4 + sc[0] + (thinking ? 0.5 : 0),
        cam.y + this.mouse.y * 0.3 + sc[1] + (thinking ? 0.45 : 0), cam.z);
      if (this.mouthSource) {
        const [lvl, vowel] = this.mouthSource();
        this.setMouth(lvl, vowel);
      }
      this.applyPose(this.t);
      this.updateExpressions(dt);
      this.face?.beforeVrmUpdate();
      this.vrm.update(dt);
      this.face?.update(dt, this.t, this.speaking);
      this.wardrobe?.update(this.t);
      if (this.t > (this.nextIdle || 20)) { this.nextIdle = this.t + 25 + Math.random() * 30; this.idleAction(); }
    }
    this.renderer.render(this.scene, this.camera);
    if (this.mascot && (this.frameNo = (this.frameNo || 0) + 1) % 4 === 0) {
      const over = this.hitTest();
      if (over !== this.overModel) {
        this.overModel = over;
        this.onHoverChange?.(over);
      }
    }
  }
}
