import { Container } from 'pixi.js';

/**
 * 层级管理器 - 将场景分层渲染
 * 管理背景/中景/前景容器
 */
export class LayerManager {
  readonly bg: Container;
  readonly mg: Container;
  readonly fg: Container;

  constructor(root: Container) {
    this.bg = new Container();
    this.mg = new Container();
    this.fg = new Container();
    this.bg.label = 'background';
    this.mg.label = 'midground';
    this.fg.label = 'foreground';
    root.addChild(this.bg, this.mg, this.fg);
  }
}
