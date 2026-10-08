/* 办公室拟人动画原型 —— 渲染层
 *
 * 只做一件事：把 story.js 给的状态快照画出来。
 * 不持有时间轴、不问「该播哪条了」——每帧重新问一次 sceneAt()。
 * 这样动画播多久由本层决定，逻辑层永远不用猜（Agentshire 的教训）。
 */

const TAU = Math.PI * 2;

/** 每个角色的可变外观参数，保证「同一个角色跨状态长得一样」。 */
const LOOK = {
  owner:       { skin: '#f7d9c4', hair: '#4a4033', cloth: '#f2b632', eyes: '#2b2b2b' },
  coordinator: { skin: '#f7d9c4', hair: '#2f3550', cloth: '#f2b632', eyes: '#2b2b2b' },
  opencode:    { skin: '#fbdcc8', hair: '#1f4d47', cloth: '#37c2a8', eyes: '#2b2b2b' },
  workbuddy:   { skin: '#f7d9c4', hair: '#4a3a1e', cloth: '#e8a33d', eyes: '#2b2b2b' },
  qoder:       { skin: '#fbdcc8', hair: '#3b2f5c', cloth: '#a78bfa', eyes: '#2b2b2b' },
  trae:        { skin: '#f9dfcb', hair: '#5c2130', cloth: '#fb7185', eyes: '#2b2b2b' },
  codex:       { skin: '#f7d9c4', hair: '#3f4d16', cloth: '#a3e635', eyes: '#2b2b2b' },
  claudecode:  { skin: '#fbdcc8', hair: '#2c2f52', cloth: '#818cf8', eyes: '#3a2a1a' },
};

export class Scene {
  constructor(canvas) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.t = 0;
    this.chars = [];
    this.resize();
    addEventListener('resize', () => this.resize());
  }

  resize() {
    const r = this.cv.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    this.cv.width = Math.max(1, Math.round(r.width * dpr));
    this.cv.height = Math.max(1, Math.round(r.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = r.width;
    this.h = r.height;
  }

  /** 每帧调用。desks/states 由 story.js 给出。 */
  draw(desks, states, dtMs) {
    const { ctx, w, h } = this;
    this.t += Math.min(dtMs, 50) / 1000;
    ctx.clearRect(0, 0, w, h);

    this.#floor(w, h);
    this.#wall(w, h);

    for (const d of desks) {
      const x = d.x * w;
      const y = d.y * h;
      this.#desk(x, y, d);
    }
    // 人在桌子之后画，保证「站起来」时身体在桌子前面
    for (const d of desks) {
      const x = d.x * w;
      const y = d.y * h;
      const s = states[d.key] || { state: 'idle', label: '' };
      this.#person(x, y, s, d, x / w > 0.55);
    }
    return this;
  }

  // ---------------------------------------------------------------- 场景

  #floor(w, h) {
    const { ctx } = this;
    const g = ctx.createLinearGradient(0, h * 0.28, 0, h);
    g.addColorStop(0, '#efe6d8');
    g.addColorStop(1, '#d9cdb8');
    ctx.fillStyle = g;
    ctx.fillRect(0, h * 0.28, w, h * 0.72);

    // 地砖缝：横竖各几条，做出「办公室地板」的读感
    ctx.strokeStyle = 'rgba(120,100,70,.12)';
    ctx.lineWidth = 1;
    for (let i = 1; i < 7; i += 1) {
      const y = h * 0.28 + (h * 0.72 * i) / 7;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    for (let i = 1; i < 12; i += 1) {
      const x = (w * i) / 12;
      ctx.beginPath(); ctx.moveTo(x, h * 0.28); ctx.lineTo(x, h * 1.02); ctx.stroke();
    }
  }

  #wall(w, h) {
    const { ctx } = this;
    const g = ctx.createLinearGradient(0, 0, 0, h * 0.3);
    g.addColorStop(0, '#cfd9e6');
    g.addColorStop(1, '#e6ebf2');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h * 0.3);
    ctx.fillStyle = 'rgba(255,255,255,.35)';
    ctx.fillRect(0, h * 0.28, w, 3);
    // 远处窗户
    for (let i = 0; i < 3; i += 1) {
      const x = w * (0.08 + i * 0.3);
      ctx.fillStyle = 'rgba(255,255,255,.55)';
      ctx.fillRect(x, h * 0.06, w * 0.2, h * 0.16);
      ctx.strokeStyle = 'rgba(90,110,140,.35)';
      ctx.strokeRect(x, h * 0.06, w * 0.2, h * 0.16);
    }
  }

  #desk(x, y, d) {
    const { ctx } = this;
    const dw = 118;
    const dh = 46;
    ctx.fillStyle = 'rgba(0,0,0,.06)';
    ctx.beginPath();
    ctx.ellipse(x, y + 44, dw * 0.55, 10, 0, 0, TAU);
    ctx.fill();

    ctx.fillStyle = d.accent;
    ctx.globalAlpha = 0.9;
    this.#roundRect(x - dw / 2, y - dh / 2 + 26, dw, 10, 3);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#b98f5e';
    ctx.fillRect(x - dw / 2 + 6, y - dh / 2 + 36, 8, 26);
    ctx.fillRect(x + dw / 2 - 14, y - dh / 2 + 36, 8, 26);

    // 电脑：屏幕上跑「输入代码」小动画
    const mx = x - dw / 2 + 34;
    const my = y - 40;
    ctx.fillStyle = '#2b2f3a';
    this.#roundRect(mx - 26, my - 18, 52, 34, 3);
    ctx.fill();
    ctx.fillStyle = '#101623';
    ctx.fillRect(mx - 22, my - 14, 44, 26);
    const phase = (this.t * 3.2) % 1;
    const bars = 5;
    for (let i = 0; i < bars; i += 1) {
      const on = i < Math.floor(phase * bars) + 1;
      ctx.fillStyle = on ? d.accent : 'rgba(120,140,170,.25)';
      const bw = 30 - i * 3.2;
      ctx.fillRect(mx - 16, my - 9 + i * 4.4, Math.max(4, bw), 2.4);
    }
    ctx.fillStyle = '#2b2f3a';
    ctx.fillRect(mx - 5, my + 16, 10, 4);
    ctx.fillRect(mx - 12, my + 20, 24, 3);

    // 名牌
    ctx.fillStyle = 'rgba(255,255,255,.85)';
    this.#roundRect(x + dw / 2 - 46, y + 18, 40, 13, 3);
    ctx.fill();
    ctx.fillStyle = '#4a4436';
    ctx.font = '9px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(d.name, x + dw / 2 - 26, y + 25);
  }

  // ---------------------------------------------------------------- 人

  /**
   * 画一个 Q 版小人。
   *
   * 刻意用「同一套骨架 + 按状态改几个参数」而不是每个状态各画一张：
   * 这样才能保证同一个角色跨状态长相一致，也是将来把立绘贴进来的位置
   * （立绘只替换 head+torso，骨架不动）。
   */
  #person(x, y, s, d, mirror) {
    const { ctx } = this;
    const L = LOOK[d.key] || LOOK.opencode;
    const M = mirror ? -1 : 1;
    const st = s.state;

    // 起身类状态把整个人抬高，并加一点「得意」的上下浮动
    let lift = 0;
    let bob = Math.sin(this.t * 2.1 + d.index) * 2.4;
    let lean = 0;
    if (st === 'done') { lift = -18; bob = Math.sin(this.t * 5) * 2; }
    if (st === 'helping') { lift = -10; bob = Math.sin(this.t * 8) * 3; }
    if (st === 'reviewing') { lift = -8; }
    if (st === 'typing' || st === 'snack' || st === 'snoop') { bob *= 0.35; lean = st === 'snoop' ? 0.18 : 0.1; }

    const cy = y - 62 + lift;
    ctx.save();
    ctx.translate(x, cy);
    ctx.scale(M, 1);

    // 阴影
    ctx.fillStyle = 'rgba(0,0,0,.10)';
    ctx.beginPath();
    ctx.ellipse(0, 58 - lift, 26, 7, 0, 0, TAU);
    ctx.fill();

    this.#body(L, st, lean, bob);
    this.#head(L, st, lean, bob, d, s);
    this.#arms(L, st, this.t, bob);
    ctx.restore();

    // 状态气泡（不随镜像翻转，否则字会反）
    if (s.label) {
      const onTop = st === 'done' || st === 'helping' || st === 'reviewing';
      const by = cy - 66 + (onTop ? -6 : 0);
      ctx.font = '600 11px system-ui, sans-serif';
      const tw = ctx.measureText(s.label).width + 14;
      ctx.fillStyle = st === 'stuck' ? '#fee2e2' : st === 'done' ? '#dcfce7' : 'rgba(17,24,39,.82)';
      this.#roundRect(x - tw / 2, by - 11, tw, 20, 10);
      ctx.fill();
      ctx.fillStyle = st === 'stuck' ? '#991b1b' : st === 'done' ? '#166534' : '#fff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(s.label, x, by - 1);
      if (st === 'stuck') {
        // 恼怒：气泡旁抖动的怒纹
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 2;
        for (let i = 0; i < 3; i += 1) {
          const a = this.t * 6 + (i * TAU) / 3;
          ctx.beginPath();
          ctx.moveTo(x + tw / 2 + 6 + Math.cos(a) * 5, by - 4 + Math.sin(a) * 5);
          ctx.lineTo(x + tw / 2 + 12 + Math.cos(a) * 5, by - 4 + Math.sin(a) * 5);
          ctx.stroke();
        }
      }
      if (st === 'done') {
        // 「任务完成」：星星
        for (let i = 0; i < 4; i += 1) {
          const a = this.t * 2 + i;
          ctx.fillStyle = `rgba(250,204,21,${0.9 - i * 0.18})`;
          ctx.beginPath();
          ctx.arc(x + Math.cos(a) * 26, by - 14 + Math.sin(a) * 10, 3.2 - i * 0.5, 0, TAU);
          ctx.fill();
        }
      }
    }
  }

  #body(L, st, lean, bob) {
    const { ctx } = this;
    ctx.save();
    ctx.rotate(lean * 0.12);
    const y0 = 22 + bob * 0.4;
    // 椅背
    ctx.fillStyle = 'rgba(60,60,70,.35)';
    this.#roundRect(-26, y0 + 4, 52, 30, 8);
    ctx.fill();
    // 身体
    ctx.fillStyle = L.cloth;
    this.#roundRect(-19, y0, 38, 40, 13);
    ctx.fill();
    // 衣领
    ctx.fillStyle = 'rgba(255,255,255,.85)';
    ctx.beginPath();
    ctx.moveTo(-7, y0 + 1); ctx.lineTo(7, y0 + 1); ctx.lineTo(0, y0 + 11); ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  #head(L, st, lean, bob, d, s) {
    const { ctx } = this;
    ctx.save();
    const hy = 8 + bob;
    ctx.translate(0, hy);
    ctx.rotate(lean * 0.35 + (st === 'sleep' ? 0.22 : 0));

    // 后发
    ctx.fillStyle = L.hair;
    ctx.beginPath();
    ctx.ellipse(0, -2, 21, 21, 0, 0, TAU);
    ctx.fill();
    // 脸
    ctx.fillStyle = L.skin;
    ctx.beginPath();
    ctx.ellipse(0, 0, 17.5, 17.5, 0, 0, TAU);
    ctx.fill();
    // 刘海
    ctx.fillStyle = L.hair;
    ctx.beginPath();
    ctx.arc(0, -3, 17.5, Math.PI * 1.06, Math.PI * 1.94);
    ctx.closePath();
    ctx.fill();
    // 呆毛/发饰（按角色区分）
    ctx.fillStyle = L.cloth;
    if (d.vibe === 'star') {
      ctx.beginPath();
      for (let i = 0; i < 5; i += 1) {
        const a = -Math.PI / 2 + (i - 2) * 0.5;
        const ox = -10 + i * 5;
        const oy = -19 + Math.sin(this.t * 2 + i) * 1.5;
        ctx.lineTo(ox, oy);
      }
      ctx.closePath(); ctx.fill();
    } else if (d.vibe === 'bun') {
      ctx.beginPath(); ctx.arc(0, -21, 6, 0, TAU); ctx.fill();
    } else if (d.vibe === 'glasses') {
      ctx.strokeStyle = 'rgba(30,30,40,.75)';
      ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(-6, 1, 5, 0, TAU); ctx.stroke();
      ctx.beginPath(); ctx.arc(6, 1, 5, 0, TAU); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-1, 1); ctx.lineTo(1, 1); ctx.stroke();
    } else if (d.vibe === 'cool') {
      // 短发：把后发压低
      ctx.fillStyle = L.hair;
      ctx.beginPath(); ctx.arc(0, -4, 18, Math.PI * 1.1, Math.PI * 1.9); ctx.fill();
    } else {
      ctx.beginPath(); ctx.arc(-13, -8, 4.5, 0, TAU); ctx.fill();
      ctx.beginPath(); ctx.arc(13, -8, 4.5, 0, TAU); ctx.fill();
    }

    // 眼睛：按状态改形状（闭眼/怒目/爱心/zzz）
    const blink = Math.sin(this.t * 1.7) > 0.985;
    const sleepy = st === 'sleep';
    ctx.strokeStyle = '#1f2430';
    ctx.fillStyle = '#1f2430';
    ctx.lineWidth = 1.7;
    const eye = (ex) => {
      if (sleepy) {
        ctx.beginPath(); ctx.moveTo(ex - 4, 1); ctx.quadraticCurveTo(ex, 3.4, ex + 4, 1); ctx.stroke();
      } else if (blink) {
        ctx.beginPath(); ctx.moveTo(ex - 4, 1); ctx.lineTo(ex + 4, 1); ctx.stroke();
      } else if (st === 'stuck') {
        // 恼怒：倒八字
        ctx.beginPath(); ctx.moveTo(ex - 4.4, -1.6); ctx.lineTo(ex + 4.4, 2.4); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(ex - 4.4, 2.6); ctx.lineTo(ex + 4.4, -0.4); ctx.stroke();
      } else if (st === 'done') {
        ctx.beginPath();
        ctx.moveTo(ex - 4, 2); ctx.quadraticCurveTo(ex, -3.4, ex + 4, 2);
        ctx.quadraticCurveTo(ex, 4.4, ex - 4, 2);
        ctx.fill();
      } else if (st === 'typing' || st === 'reviewing') {
        ctx.beginPath(); ctx.arc(ex, 1, 3, 0, TAU); ctx.fill();
      } else {
        ctx.beginPath(); ctx.ellipse(ex, 0.6, 2.9, 3.6, 0, 0, TAU); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,.9)';
        ctx.beginPath(); ctx.arc(ex + 1, -0.6, 1.1, 0, TAU); ctx.fill();
        ctx.fillStyle = '#1f2430';
      }
    };
    eye(-6.4); eye(6.4);

    // 腮红
    ctx.fillStyle = 'rgba(244,114,124,.24)';
    ctx.beginPath(); ctx.arc(-11, 4.6, 3.4, 0, TAU); ctx.fill();
    ctx.beginPath(); ctx.arc(11, 4.6, 3.4, 0, TAU); ctx.fill();

    // 嘴
    ctx.strokeStyle = '#b4553f';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (st === 'snack' || st === 'snoop') { ctx.arc(0, 5.4, 2.6, 0, Math.PI); }
    else if (st === 'stuck') { ctx.arc(0, 8.6, 2.6, Math.PI, TAU); }
    else if (sleepy) { ctx.ellipse(0, 6, 2.2, 2.8, 0, 0, TAU); }
    else { ctx.arc(0, 3.4, 3, 0.18 * Math.PI, 0.82 * Math.PI); }
    ctx.stroke();

    if (sleepy) {
      ctx.fillStyle = '#1f2430';
      ctx.font = '700 10px system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText('z', 15, -8 + Math.sin(this.t * 2) * 2);
      ctx.fillText('z', 20, -14 + Math.sin(this.t * 2 + 1) * 2);
    }
    if (st === 'helping') {
      // 头顶感叹号
      ctx.fillStyle = '#f59e0b';
      ctx.fillRect(-1.6, -34, 3.2, 9);
      ctx.beginPath(); ctx.arc(0, -22.4, 1.9, 0, TAU); ctx.fill();
    }
    ctx.restore();
  }

  #arms(L, st, t, bob) {
    const { ctx } = this;
    const swing = Math.sin(t * (st === 'typing' ? 13 : 3)) * (st === 'typing' ? 4.5 : 2);
    ctx.strokeStyle = L.cloth;
    ctx.lineWidth = 7;
    ctx.lineCap = 'round';
    const shoulder = (sx) => {
      ctx.beginPath();
      ctx.moveTo(sx, 24 + bob * 0.4);
      if (st === 'typing' || st === 'reviewing') {
        // 双手前伸敲键盘
        ctx.lineTo(sx + (sx < 0 ? -6 : 6), 38 + bob * 0.4);
        ctx.lineTo(sx + (sx < 0 ? -15 : 15), 42 + swing * 0.4);
      } else if (st === 'done') {
        // 伸懒腰：双手上举
        ctx.lineTo(sx + (sx < 0 ? -6 : 6), 16);
        ctx.lineTo(sx + (sx < 0 ? -12 : 12), 2 + swing * 0.3);
      } else if (st === 'stuck') {
        // 抓耳挠腮
        ctx.lineTo(sx + (sx < 0 ? -3 : 3), 30);
        ctx.lineTo(sx + (sx < 0 ? -9 : 9), 20 + swing * 0.5);
      } else if (st === 'snack') {
        // 一手往嘴里送
        ctx.lineTo(sx + (sx < 0 ? -10 : 14), 34);
        ctx.lineTo(sx + (sx < 0 ? -4 : 8), 22);
      } else if (st === 'snoop') {
        // 一手挡在屏幕边做贼
        ctx.lineTo(sx + (sx < 0 ? -20 : 20), 30);
        ctx.lineTo(sx + (sx < 0 ? -10 : 10), 34 + swing * 0.3);
      } else {
        ctx.lineTo(sx + (sx < 0 ? -9 : 9), 34 + swing);
      }
      ctx.stroke();
    };
    shoulder(-16);
    shoulder(16);

    // 爪子/袖口
    ctx.fillStyle = L.skin;
    const hand = (sx, hx, hy) => { ctx.beginPath(); ctx.arc(sx + hx, hy, 3.4, 0, TAU); ctx.fill(); };
    if (st === 'typing' || st === 'reviewing') { hand(-16, -15, 42); hand(16, 15, 42); }
    if (st === 'done') { hand(-16, -12, 2); hand(16, 12, 2); }
    if (st === 'snack') { hand(16, 8, 22); hand(-16, -4, 34); }
  }

  #roundRect(x, y, w, h, r) {
    const { ctx } = this;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
}
