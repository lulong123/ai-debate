import { Character } from './Character';

/**
 * 辩手角色
 * 参数化：通过颜色区分不同辩手
 */
export class DebaterChar extends Character {
  constructor(config: {
    name: string;
    color: number;
    seatIndex: number;
    positionId: string;
  }) {
    const bodyColor = darkenColor(config.color, 0.5);
    super({
      name: config.name,
      role: `辩手 ${config.seatIndex + 1}`,
      headColor: config.color,
      bodyColor,
      legColor: darkenColor(config.color, 0.3),
      accentColor: config.color,
    });
    this._positionId = config.positionId;
  }

  private _positionId: string;
  get positionId() { return this._positionId; }
}

/** 将颜色变暗 */
function darkenColor(color: number, factor: number): number {
  const r = ((color >> 16) & 0xff) * factor;
  const g = ((color >> 8) & 0xff) * factor;
  const b = (color & 0xff) * factor;
  return (Math.round(r) << 16) | (Math.round(g) << 8) | Math.round(b);
}
