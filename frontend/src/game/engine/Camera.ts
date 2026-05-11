import { Application } from 'pixi.js';

/**
 * Canvas 自适应 + DPR 适配
 * 设计分辨率 1280x720，等比缩放
 */
export class Camera {
  private app: Application;
  private designW = 1280;
  private designH = 720;

  constructor(app: Application) {
    this.app = app;
  }

  /** 监听容器尺寸变化，自动调整 canvas 大小 */
  observe(container: HTMLElement) {
    const ro = new ResizeObserver(() => this.resize(container));
    ro.observe(container);
    this.resize(container);
    return () => ro.disconnect();
  }

  private resize(container: HTMLElement) {
    const w = container.clientWidth;
    const h = container.clientHeight;
    this.app.renderer.resize(w, h);
  }

  /** 将世界坐标转换为屏幕居中坐标 */
  getStageOffset(): { x: number; y: number } {
    const sw = this.app.renderer.width / this.designW;
    const sh = this.app.renderer.height / this.designH;
    const scale = Math.min(sw, sh);
    return {
      x: (this.app.renderer.width - this.designW * scale) / 2,
      y: (this.app.renderer.height - this.designH * scale) / 2,
    };
  }

  getScale(): number {
    const sw = this.app.renderer.width / this.designW;
    const sh = this.app.renderer.height / this.designH;
    return Math.min(sw, sh);
  }
}
