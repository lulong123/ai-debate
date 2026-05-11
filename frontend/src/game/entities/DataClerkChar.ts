import { Character } from './Character';
import { Graphics } from 'pixi.js';

/**
 * 数据研究员角色
 * 特征：绿色头部 + 深绿身体 + 放大镜
 */
export class DataClerkChar extends Character {
  constructor() {
    super({
      name: '研究员',
      role: 'Data Clerk',
      headColor: 0x34d399,
      bodyColor: 0x065f46,
      legColor: 0x064e3b,
      accentColor: 0x34d399,
    });
  }

  protected redraw() {
    super.redraw();
    this.drawGlasses();
    this.drawMagnifier();
  }

  /** 眼镜 */
  private drawGlasses() {
    const g = new Graphics();
    // 左镜框
    g.arc(-3, -1, 4, 0, Math.PI * 2).stroke({ color: '#94a3b8', width: 1.5 });
    // 右镜框
    g.arc(4, -1, 4, 0, Math.PI * 2).stroke({ color: '#94a3b8', width: 1.5 });
    // 鼻梁
    g.moveTo(1, -1)
      .lineTo(0, -1)
      .stroke({ color: '#94a3b8', width: 1.5 });
    this.container.addChild(g);
  }

  /** 放大镜 */
  private drawMagnifier() {
    const g = new Graphics();
    // 手柄
    g.rect(8, 2, 2, 10).fill('#8B7355');
    // 镜片
    g.arc(9, -1, 5, 0, Math.PI * 2).stroke({ color: '#60a5fa', width: 2 });
    // 镜片反光
    g.arc(9, -1, 4, 0, Math.PI * 2).fill('rgba(96,165,250,0.15)');
    this.container.addChild(g);
  }
}
