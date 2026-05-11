import { Character } from './Character';
import { Graphics, Text, TextStyle } from 'pixi.js';

/**
 * 评委角色
 * 特征：紫色头部 + 深紫身体 + 记分牌
 */
export class ScorerChar extends Character {
  private scoreText: Text;
  private scoreDisplay: Graphics;

  constructor() {
    super({
      name: '评委',
      role: 'Scorer',
      headColor: 0xa78bfa,
      bodyColor: 0x5b21b6,
      legColor: 0x4c1d95,
      accentColor: 0xa78bfa,
    });

    // 记分牌
    this.scoreDisplay = new Graphics();
    this.scoreDisplay.x = 18;
    this.scoreDisplay.y = -10;

    // 分数文本
    this.scoreText = new Text({
      text: '--',
      style: new TextStyle({
        fontFamily: 'monospace',
        fontSize: 14,
        fill: '#fbbf24',
        fontWeight: 'bold',
      }),
    });
    this.scoreText.anchor.set(0.5);
    this.scoreText.x = 18;
    this.scoreText.y = -8;

    this.container.addChild(this.scoreDisplay, this.scoreText);
  }

  /** 显示分数 */
  showScore(score: number) {
    this.scoreText.text = score.toFixed(1);

    // 重绘记分牌
    const g = this.scoreDisplay;
    g.clear();
    g.roundRect(8, -18, 20, 22, 2).fill('#1e293b');
    g.roundRect(8, -18, 20, 22, 2).stroke({ color: '#fbbf24', width: 1 });
  }

  protected redraw() {
    super.redraw();
    // 画笔道具
    const g = new Graphics();
    g.rect(8, 5, 2, 10).fill('#fbbf24');
    g.rect(7, 3, 4, 3).fill(0x1e293b);
    this.container.addChild(g);
  }
}
