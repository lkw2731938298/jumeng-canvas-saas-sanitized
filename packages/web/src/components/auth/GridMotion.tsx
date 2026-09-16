"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { gsap } from "gsap";

import "./GridMotion.css";

interface GridMotionProps {
  items?: (string | ReactNode)[];
  gradientColor?: string;
  className?: string;
}

const TOTAL_ITEMS = 28;
const ROWS = 4;
const COLS = 7;

export function GridMotion({
  items = [],
  gradientColor = "black",
  className,
}: GridMotionProps) {
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);
  const mouseXRef = useRef(0);

  const combinedItems =
    items.length > 0
      ? items.slice(0, TOTAL_ITEMS)
      : Array.from({ length: TOTAL_ITEMS }, (_, index) => `Item ${index + 1}`);

  useEffect(() => {
    mouseXRef.current = window.innerWidth / 2;
    gsap.ticker.lagSmoothing(0);

    const handleMouseMove = (event: MouseEvent) => {
      mouseXRef.current = event.clientX;
    };

    const updateMotion = () => {
      const maxMoveAmount = 300;
      const baseDuration = 0.8;
      const inertiaFactors = [0.6, 0.4, 0.3, 0.2];

      rowRefs.current.forEach((row, index) => {
        if (!row) return;
        const direction = index % 2 === 0 ? 1 : -1;
        const moveAmount =
          ((mouseXRef.current / window.innerWidth) * maxMoveAmount - maxMoveAmount / 2) * direction;

        gsap.to(row, {
          x: moveAmount,
          duration: baseDuration + inertiaFactors[index % inertiaFactors.length],
          ease: "power3.out",
          overwrite: "auto",
        });
      });
    };

    const removeAnimationLoop = gsap.ticker.add(updateMotion);
    window.addEventListener("mousemove", handleMouseMove);

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      removeAnimationLoop();
    };
  }, []);

  return (
    <div className={["grid-motion-root", className].filter(Boolean).join(" ")}>
      <section
        className="grid-motion-intro"
        style={{
          background: `radial-gradient(circle, ${gradientColor} 0%, transparent 100%)`,
        }}
      >
        <div className="grid-motion-container">
          {Array.from({ length: ROWS }, (_, rowIndex) => (
            <div
              key={rowIndex}
              className="grid-motion-row"
              ref={(element) => {
                rowRefs.current[rowIndex] = element;
              }}
            >
              {Array.from({ length: COLS }, (_, itemIndex) => {
                const content = combinedItems[rowIndex * COLS + itemIndex];
                // 含 blob:（登录页降采样后的本地 URL），勿误判为文本节点
                const isImageUrl =
                  typeof content === "string" &&
                  (content.startsWith("/") ||
                    content.startsWith("http://") ||
                    content.startsWith("https://") ||
                    content.startsWith("blob:"));

                return (
                  <div key={itemIndex} className="grid-motion-row__item">
                    <div className="grid-motion-row__item-inner">
                      {isImageUrl ? (
                        <div
                          className="grid-motion-row__item-img"
                          style={{ backgroundImage: `url(${content})` }}
                        />
                      ) : (
                        <div className="grid-motion-row__item-content">{content}</div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
