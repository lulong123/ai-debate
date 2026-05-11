import { Application } from 'pixi.js';

export class GameLoop {
  app: Application;
  private _running = false;

  static readonly DESIGN_W = 1280;
  static readonly DESIGN_H = 720;

  constructor() {
    this.app = new Application();
  }

  async init(canvas: HTMLCanvasElement) {
    await this.app.init({
      canvas,
      background: '#0c1222',
      antialias: false,
      resolution: 1,
      width: GameLoop.DESIGN_W,
      height: GameLoop.DESIGN_H,
      // 不使用 autoDensity - 我们手动控制 CSS 尺寸
      autoDensity: false,
    });
  }

  start() {
    if (this._running) return;
    this._running = true;
    this.app.ticker.start();
  }

  stop() {
    this._running = false;
    this.app.ticker.stop();
  }

  destroy() {
    this.stop();
    this.app.destroy(true);
  }

  get running() { return this._running; }
}
