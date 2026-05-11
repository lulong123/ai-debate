/** 座位信息 */
export interface SeatInfo {
  x: number;
  y: number;
  index: number;
  /** y 越大越靠前（用于 z-index） */
  sortY: number;
}

/**
 * 动态座位计算（2-8人弧形排列）
 * 围绕椭圆桌子分布，主持人固定在底部中央
 */
export class SeatLayout {
  private cx = 640; // 椭圆中心 x
  private cy = 340; // 椭圆中心 y
  private rx = 300; // 水平半径
  private ry = 200; // 垂直半径

  /**
   * 计算座位位置
   * @param count 参与者数量（不含主持人），2-8
   * @param moderatorFirst 是否主持人占第一个位置
   */
  compute(count: number, moderatorFirst = true): SeatInfo[] {
    const total = moderatorFirst ? count + 1 : count;
    const seats: SeatInfo[] = [];

    for (let i = 0; i < total; i++) {
      // 从底部中间(270°)开始顺时针分布
      const angleDeg = 270 + i * (360 / total);
      const angle = (angleDeg * Math.PI) / 180;
      const x = this.cx + this.rx * Math.cos(angle);
      const y = this.cy + this.ry * Math.sin(angle);
      seats.push({ x, y, index: i, sortY: Math.round(y) });
    }

    return seats;
  }
}
