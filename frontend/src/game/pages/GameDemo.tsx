import { useEffect, useRef } from 'react';
import { Application, Sprite, Container, Text, TextStyle, Assets } from 'pixi.js';

const DESIGN_W = 1280;
const DESIGN_H = 720;

export function GameDemo() {
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    let destroyed = false;
    let app: Application | null = null;
    let onResize: (() => void) | null = null;

    async function boot() {
      // 创建 canvas
      const canvas = document.createElement('canvas');
      canvas.width = DESIGN_W;
      canvas.height = DESIGN_H;
      canvas.style.imageRendering = 'pixelated';
      wrapper!.appendChild(canvas);

      // CSS 缩放适配
      function fit() {
        const s = Math.min(window.innerWidth / DESIGN_W, window.innerHeight / DESIGN_H);
        canvas.style.width = `${Math.floor(DESIGN_W * s)}px`;
        canvas.style.height = `${Math.floor(DESIGN_H * s)}px`;
      }
      fit();
      onResize = fit;
      window.addEventListener('resize', fit);

      // 初始化 PixiJS
      app = new Application();
      await app.init({
        canvas,
        background: '#0a0e1a',
        antialias: false,
        resolution: 1,
        width: DESIGN_W,
        height: DESIGN_H,
        autoDensity: false,
      });
      if (destroyed) { app.destroy(true); return; }

      // === 第1层：背景（铺满） ===
      const bgTexture = await Assets.load('/assets/background.png');
      const bg = new Sprite(bgTexture);
      bg.width = DESIGN_W;
      bg.height = DESIGN_H;
      app.stage.addChild(bg);

      // === 第2层：场景容器 ===
      const scene = new Container();
      app.stage.addChild(scene);

      // 桌子参数 - 放画面中央
      const tableCX = DESIGN_W / 2;
      const tableCY = DESIGN_H / 2 + 20;
      const tableRX = 300;  // 椭圆水平半径
      const tableRY = 180;   // 椭圆垂直半径

      // 远端角色容器（先画，在桌子后面）
      const farChars = new Container();
      scene.addChild(farChars);

      // === 主持人（桌子后方中央，面朝观众） ===
      const modTexture = await Assets.load('/assets/moderator.png');
      const moderator = new Sprite(modTexture);
      moderator.anchor.set(0.5, 1); // 底部中心对齐
      moderator.x = tableCX;
      moderator.y = tableCY - tableRY + 75; // 桌子上方，靠近桌子
      // 缩放主持人：高度约 88px
      const modScale = 88 / modTexture.height;
      moderator.scale.set(modScale);
      farChars.addChild(moderator);

      // === 辩手0（桌子后方左侧） ===
      const debTexture = await Assets.load('/assets/debater_0.png');
      const debater0 = new Sprite(debTexture);
      debater0.anchor.set(0.5, 1); // 底部中心对齐
      debater0.x = tableCX - 170; // 左侧
      debater0.y = tableCY - tableRY + 105;
      const debScale = 107 / debTexture.height;
      debater0.scale.set(debScale);
      farChars.addChild(debater0);

      // === 桌子 ===
      const tableTexture = await Assets.load('/assets/table.png');
      const table = new Sprite(tableTexture);
      table.anchor.set(0.5);
      table.x = tableCX;
      table.y = tableCY;
      // 桌子缩放，宽度约670px
      const scale = 670 / tableTexture.width;
      table.scale.set(scale);
      scene.addChild(table);

      // 近端角色容器（后画，在桌子前面）
      const nearChars = new Container();
      scene.addChild(nearChars);

      // === UI层 ===
      const title = new Text({
        text: 'AI 圆桌辩论 · Pixel Demo',
        style: new TextStyle({
          fontFamily: 'monospace',
          fontSize: 14,
          fill: '#94a3b8',
        }),
      });
      title.x = 12;
      title.y = 12;
      app.stage.addChild(title);
    }

    boot();

    return () => {
      destroyed = true;
      if (onResize) window.removeEventListener('resize', onResize);
      if (app) app.destroy(true);
      const c = wrapper?.querySelector('canvas');
      if (c) c.remove();
    };
  }, []);

  return (
    <div
      ref={wrapperRef}
      style={{
        width: '100vw',
        height: '100vh',
        background: '#0a0e1a',
        overflow: 'hidden',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    />
  );
}
