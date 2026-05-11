import { Container } from 'pixi.js';

/**
 * 场景层级管理
 * 背景 / 中景 / 前景 三层容器
 */
export class SceneManager {
  readonly background: Container;
  readonly midground: Container;
  readonly foreground: Container;
  readonly root: Container;

  constructor() {
    this.root = new Container();
    this.background = new Container();
    this.midground = new Container();
    this.foreground = new Container();
    this.root.addChild(this.background, this.midground, this.foreground);
  }

  pause() {
    this.root.visible = false;
  }

  resume() {
    this.root.visible = true;
  }

  destroy() {
    this.root.destroy({ children: true });
  }
}
