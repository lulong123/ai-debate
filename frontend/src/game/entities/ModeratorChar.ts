import { Character } from './Character';
import { Graphics } from 'pixi.js';

/**
 * 主持人角色
 * 特征：金色头部 + 蓝色身体 + 法槌 + 皇冠
 */
export class ModeratorChar extends Character {
  private gavel: Graphics | null = null;
  private gavelAngle = 0;

  constructor() {
    super({
      name: '主持人',
      role: 'Moderator',
      headColor: 0xfbbf24,
      bodyColor: 0x1e40af,
      legColor: 0x1e3a8a,
      accentColor: 0xfbbf24, // 金色领带
    });
  }

  /** 敲法槌动画 */
  strikeGavel() {
    this.gavelAngle = -0.5;
    this.drawGavel();
    // 简单的回弹
    setTimeout(() => {
      this.gavelAngle = 0.3;
      this.drawGavel();
      setTimeout(() => {
        this.gavelAngle = 0;
        this.drawGavel();
      }, 100);
    }, 80);
  }

  protected redraw() {
    super.redraw();
    this.drawGavel();
    this.drawCrown();
  }

  private drawGavel() {
    if (this.gavel) {
      this.gavel.destroy();
    }
    this.gavel = new Graphics();
    this.gavel.rotation = this.gavelAngle;

    // 法槌柄
    this.gavel.rect(5, -5, 3, 15).fill('#8B7355');

    // 法槌头
    this.gavel.rect(3, -8, 7, 5).fill('#5C4033');

    this.container.addChild(this.gavel);
  }

  private drawCrown() {
    // 皇冠 - 在角色头顶
    const g = new Graphics();
    // 皇冠底座
    g.rect(-6, -20, 12, 3).fill(0xfbbf24);
    // 皇冠三个尖
    g.rect(-6, -24, 3, 4).fill(0xfbbf24);
    g.rect(-1, -26, 3, 6).fill(0xfbbf24);
    g.rect(3, -24, 3, 4).fill(0xfbbf24);
    // 宝石
    g.rect(-1, -25, 2, 2).fill(0xef4444);
    this.container.addChild(g);
  }
}
