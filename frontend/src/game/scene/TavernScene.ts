import { Graphics, Container } from 'pixi.js';
import { LayerManager } from './LayerManager';

/**
 * 像素风酒馆场景
 * 程序化绘制：深色木质背景 + 圆桌 + 蜡烛 + 装饰
 */
export class TavernScene {
  readonly root: Container;
  private layers: LayerManager;

  constructor(root: Container) {
    this.root = root;
    this.layers = new LayerManager(root);
  }

  get midground() {
    return this.layers.mg;
  }

  get foreground() {
    return this.layers.fg;
  }

  build() {
    this.drawBackground();
    this.drawTable();
    this.drawDecorations();
  }

  /** 深色木质酒馆背景 */
  private drawBackground() {
    const bg = new Graphics();

    // 深色渐变背景（用多个矩形模拟渐变）
    const colors = ['#0a0e1a', '#0e1425', '#121a2e', '#0e1425', '#0a0e1a'];
    const h = 720 / colors.length;
    colors.forEach((c, i) => {
      bg.rect(0, i * h, 1280, h + 1).fill(c);
    });

    // 砖墙纹理
    for (let row = 0; row < 18; row++) {
      const y = row * 40;
      const offset = row % 2 === 0 ? 0 : 32;
      bg.moveTo(0, y);
      bg.lineTo(1280, y);
      for (let col = 0; col < 22; col++) {
        const x = col * 64 + offset;
        bg.moveTo(x, y);
        bg.lineTo(x, y + 40);
      }
    }
    bg.stroke({ color: '#1a2240', width: 1 });

    // 地板 - 深色木纹
    bg.rect(0, 480, 1280, 240).fill('#15102a');

    // 地板线条
    for (let i = 0; i < 8; i++) {
      const x = i * 160 + (i % 2 === 0 ? 0 : 40);
      bg.moveTo(x, 480);
      bg.lineTo(x + 80, 720);
    }
    bg.stroke({ color: '#1c1535', width: 1 });

    this.layers.bg.addChild(bg);
  }

  /** 圆桌 - 椭圆形木桌 + 桌腿 */
  drawTable() {
    const table = new Graphics();

    // 桌子阴影
    table.ellipse(640, 355, 185, 115).fill('rgba(0,0,0,0.3)');

    // 桌子侧面/厚度
    table.ellipse(640, 352, 180, 112).fill('#1a120c');

    // 桌面
    table.ellipse(640, 340, 180, 110).fill('#3d2e22');

    // 桌面高光
    table.ellipse(635, 330, 140, 80).fill('#4a3728');

    // 桌面木纹环
    table.ellipse(640, 340, 140, 85).stroke({ color: 'rgba(255,255,255,0.04)', width: 1 });
    table.ellipse(640, 340, 100, 60).stroke({ color: 'rgba(255,255,255,0.04)', width: 1 });

    // 桌面高光弧
    table.ellipse(620, 315, 80, 30).fill('rgba(255,255,255,0.03)');

    // 桌腿
    table.rect(632, 440, 16, 60).fill('#2d1f15');
    // 桌脚底座
    table.ellipse(640, 500, 30, 10).fill('#241a10');

    this.layers.mg.addChild(table);
  }

  /** 装饰：蜡烛 + 画框 */
  private drawDecorations() {
    // 左侧蜡烛
    this.drawCandle(200, 420);
    // 右侧蜡烛
    this.drawCandle(1080, 420);

    // 墙上画框 - 左
    this.drawFrame(180, 120, 100, 70);
    // 墙上画框 - 右
    this.drawFrame(1000, 100, 120, 80);

    // 桌上卷轴/纸张装饰
    this.drawTablePapers();
  }

  private drawCandle(x: number, y: number) {
    const g = new Graphics();

    // 烛台
    g.rect(x - 4, y, 8, 30).fill('#4a3728');
    // 烛台底座
    g.ellipse(x, y + 30, 12, 4).fill('#3d2e22');

    // 蜡烛
    g.rect(x - 3, y - 16, 6, 16).fill('#e8d5b0');

    // 火焰 - 外焰（橙）
    g.ellipse(x, y - 22, 5, 8).fill('#ff8c00');

    // 火焰 - 内焰（黄）
    g.ellipse(x, y - 21, 3, 5).fill('#ffd700');

    // 火焰 - 核心（白）
    g.ellipse(x, y - 20, 1.5, 3).fill('#fff8dc');

    this.layers.mg.addChild(g);
  }

  private drawFrame(x: number, y: number, w: number, h: number) {
    const g = new Graphics();

    // 画框
    g.rect(x - 3, y - 3, w + 6, h + 6).fill('#3d2e22');
    // 画面（深色）
    g.rect(x, y, w, h).fill('#1a1520');
    // 画面内容（简单风景暗示）
    g.rect(x, y + h * 0.6, w, h * 0.4).fill('#1e2530');
    g.moveTo(x, y + h * 0.6)
      .lineTo(x + w * 0.3, y + h * 0.3)
      .lineTo(x + w * 0.6, y + h * 0.55)
      .lineTo(x + w, y + h * 0.4)
      .lineTo(x + w, y + h * 0.6)
      .closePath()
      .fill('#253040');

    this.layers.bg.addChild(g);
  }

  private drawTablePapers() {
    const g = new Graphics();

    // 桌面上的文件/纸张（小矩形）
    g.rect(590, 310, 30, 40).fill('rgba(255,255,255,0.04)');
    g.rect(670, 325, 25, 35).fill('rgba(255,255,255,0.03)');
    g.rect(630, 355, 28, 32).fill('rgba(255,255,255,0.035)');

    this.layers.mg.addChild(g);
  }

  destroy() {
    this.root.destroy({ children: true });
  }
}
