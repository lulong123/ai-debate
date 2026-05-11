import { Container, Graphics, Text, TextStyle } from 'pixi.js';

/** 角色状态 */
export type CharState = 'IDLE' | 'THINKING' | 'SPEAKING' | 'REACTING' | 'COOLDOWN';

/** 角色配置 */
export interface CharacterConfig {
  name: string;
  role: string;
  /** 头发/头部颜色 */
  headColor: number;
  /** 身体颜色 */
  bodyColor: number;
  /** 腿颜色 */
  legColor?: number;
  /** 特殊装饰颜色（如领带、饰品） */
  accentColor?: number;
}

/**
 * 角色基类
 * 程序化像素绘制 + 状态机 + 动画
 * 约 16x24 px 像素小人，放大到约 48x72 显示
 */
export class Character {
  readonly container: Container;
  readonly config: CharacterConfig;

  private _state: CharState = 'IDLE';
  private stateTime = 0;
  private breathOffset = 0;
  private graphics: Graphics;
  private nameTag: Text;
  private roleTag: Text;
  private _seatX = 0;
  private _seatY = 0;
  private baseY = 0;
  private standOffset = 0;
  private targetStandOffset = 0;

  constructor(config: CharacterConfig) {
    this.config = config;
    this.container = new Container();
    this.container.label = config.name;

    this.graphics = new Graphics();
    this.container.addChild(this.graphics);

    // 名字标签
    this.nameTag = new Text({
      text: config.name,
      style: new TextStyle({
        fontFamily: 'monospace',
        fontSize: 11,
        fill: this.colorToHex(config.headColor),
        fontWeight: 'bold',
      }),
    });
    this.nameTag.anchor.set(0.5);
    this.nameTag.y = -60;

    // 角色标签
    this.roleTag = new Text({
      text: config.role,
      style: new TextStyle({
        fontFamily: 'monospace',
        fontSize: 8,
        fill: '#64748b',
      }),
    });
    this.roleTag.anchor.set(0.5);
    this.roleTag.y = -48;

    this.container.addChild(this.nameTag, this.roleTag);
  }

  /** 设置座位位置 */
  setPosition(x: number, y: number) {
    this._seatX = x;
    this._seatY = y;
    this.baseY = y;
    this.container.x = x;
    this.container.y = y;
  }

  get seatX() { return this._seatX; }
  get seatY() { return this._seatY; }

  /** 设置状态 */
  setState(state: CharState) {
    if (this._state === state) return;
    this._state = state;
    this.stateTime = 0;

    // 根据状态调整站立偏移
    switch (state) {
      case 'SPEAKING':
        this.targetStandOffset = -20; // 站起来
        break;
      case 'THINKING':
        this.targetStandOffset = -8; // 稍微抬起
        break;
      case 'REACTING':
        this.targetStandOffset = -5;
        break;
      default:
        this.targetStandOffset = 0; // 坐下
    }

    this.redraw();
  }

  get state() { return this._state; }

  /** 每帧更新 */
  update(delta: number) {
    this.stateTime += delta;

    // 呼吸动画 - IDLE 状态微动
    if (this._state === 'IDLE') {
      this.breathOffset = Math.sin(this.stateTime * 0.05) * 1.5;
    } else if (this._state === 'THINKING') {
      this.breathOffset = Math.sin(this.stateTime * 0.08) * 2;
    } else {
      this.breathOffset = 0;
    }

    // 平滑站立过渡
    this.standOffset += (this.targetStandOffset - this.standOffset) * 0.1;
    this.container.y = this.baseY + this.standOffset + this.breathOffset;

    // 思考状态 - 上下点头
    if (this._state === 'THINKING') {
      // 轻微左右摇摆
      this.container.x = this._seatX + Math.sin(this.stateTime * 0.03) * 2;
    } else {
      this.container.x = this._seatX;
    }
  }

  /** 重绘角色 */
  protected redraw() {
    const g = this.graphics;
    g.clear();

    const isStanding = this._state === 'SPEAKING';
    const scale = 3; // 像素放大倍率
    const cfg = this.config;

    // 阴影（椭圆形）
    g.ellipse(0, isStanding ? 36 : 30, 12 * scale / 3, 4 * scale / 3).fill('rgba(0,0,0,0.3)');

    if (isStanding) {
      this.drawStanding(g, scale, cfg);
    } else {
      this.drawSeated(g, scale, cfg);
    }
  }

  /** 绘制站立姿态 */
  private drawStanding(g: Graphics, s: number, cfg: CharacterConfig) {
    // 腿
    g.rect(-3 * s, 16 * s, 2 * s, 12 * s).fill(cfg.legColor ?? 0x1e3a8a);
    g.rect(1 * s, 16 * s, 2 * s, 12 * s).fill(cfg.legColor ?? 0x1e3a8a);

    // 身体
    g.rect(-4 * s, 4 * s, 8 * s, 14 * s).fill(cfg.bodyColor);

    // 装饰
    if (cfg.accentColor !== undefined) {
      g.rect(-1 * s, 5 * s, 2 * s, 6 * s).fill(cfg.accentColor);
    }

    // 手臂
    // 左手 - 举起（说话姿势）
    g.rect(-6 * s, 5 * s, 2 * s, 10 * s).fill(cfg.bodyColor);
    // 右手
    g.rect(4 * s, 6 * s, 2 * s, 8 * s).fill(cfg.bodyColor);

    // 头
    g.arc(0, 0, 5 * s, 0, Math.PI * 2).fill(cfg.headColor);

    // 眼睛
    g.rect(-2 * s, -1 * s, s, s).fill(0x1e293b);
    g.rect(1 * s, -1 * s, s, s).fill(0x1e293b);

    // 嘴巴 - 说话时张嘴
    if (this._state === 'SPEAKING') {
      g.rect(-1 * s, 2 * s, 2 * s, s).fill(0x1e293b);
    }
  }

  /** 绘制坐姿 */
  private drawSeated(g: Graphics, s: number, cfg: CharacterConfig) {
    // 腿（坐姿 - 短一些，向两侧弯曲）
    g.rect(-4 * s, 16 * s, 3 * s, 8 * s).fill(cfg.legColor ?? 0x1e3a8a);
    g.rect(1 * s, 16 * s, 3 * s, 8 * s).fill(cfg.legColor ?? 0x1e3a8a);

    // 身体（坐姿 - 稍短）
    g.rect(-4 * s, 4 * s, 8 * s, 14 * s).fill(cfg.bodyColor);

    // 装饰
    if (cfg.accentColor !== undefined) {
      g.rect(-1 * s, 5 * s, 2 * s, 6 * s).fill(cfg.accentColor);
    }

    // 手臂（放在桌上/身侧）
    g.rect(-6 * s, 8 * s, 2 * s, 6 * s).fill(cfg.bodyColor);
    g.rect(4 * s, 8 * s, 2 * s, 6 * s).fill(cfg.bodyColor);

    // 头
    g.arc(0, 0, 5 * s, 0, Math.PI * 2).fill(cfg.headColor);

    // 眼睛
    g.rect(-2 * s, -1 * s, s, s).fill(0x1e293b);
    g.rect(1 * s, -1 * s, s, s).fill(0x1e293b);

    // 思考状态 - 画问号
    if (this._state === 'THINKING') {
      g.rect(6 * s, -4 * s, s, s).fill(0xfbbf24);
      g.rect(7 * s, -3 * s, s, s).fill(0xfbbf24);
      g.rect(6 * s, -2 * s, s, s).fill(0xfbbf24);
      g.rect(6 * s, 0, s, s).fill(0xfbbf24);
    }
  }

  /** 颜色转 hex 字符串 */
  private colorToHex(color: number): string {
    return '#' + color.toString(16).padStart(6, '0');
  }

  /** 获取排序用的 Y 值 */
  get sortY() {
    return this.container.y;
  }

  destroy() {
    this.container.destroy({ children: true });
  }
}
