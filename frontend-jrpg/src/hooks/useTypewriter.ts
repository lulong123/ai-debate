import { useEffect, useRef, useState, useCallback } from "react";

const TYPE_SPEED = 40; // ms per character
const FAST_FORWARD_SPEED = 12; // ms per character when fast-forwarding

/**
 * Typewriter engine: consumes a target string and outputs characters one by one.
 * Uses a version state to trigger interval restarts on new targets or speed changes.
 */
export function useTypewriter() {
  const [displayed, setDisplayed] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [version, setVersion] = useState(0); // state to trigger effect restarts

  const targetRef = useRef("");
  const cursorRef = useRef(0);
  const speedRef = useRef(TYPE_SPEED);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onCompleteRef = useRef<(() => void) | null>(null);

  const bumpVersion = useCallback(() => setVersion((v) => v + 1), []);

  // Called when a new target is set
  const setTarget = useCallback((newTarget: string, onComplete?: () => void) => {
    targetRef.current = newTarget;
    onCompleteRef.current = onComplete ?? null;

    if (newTarget.length === 0) {
      setDisplayed("");
      cursorRef.current = 0;
      setIsTyping(false);
      return;
    }

    // If already at or past target, just set it
    if (cursorRef.current >= newTarget.length) {
      setDisplayed(newTarget);
      cursorRef.current = newTarget.length;
      setIsTyping(false);
      onCompleteRef.current?.();
      return;
    }

    setIsTyping(true);
    bumpVersion();
  }, [bumpVersion]);

  // Fast-forward: instantly show all text
  const fastForward = useCallback(() => {
    const target = targetRef.current;
    if (target) {
      setDisplayed(target);
      cursorRef.current = target.length;
      setIsTyping(false);
      onCompleteRef.current?.();
    }
  }, []);

  // Speed up: switch to fast mode
  const speedUp = useCallback(() => {
    speedRef.current = FAST_FORWARD_SPEED;
    bumpVersion();
  }, [bumpVersion]);

  // Reset speed to normal
  const resetSpeed = useCallback(() => {
    speedRef.current = TYPE_SPEED;
    bumpVersion();
  }, [bumpVersion]);

  // Typing loop — restarts when version changes
  useEffect(() => {
    // Clear any existing timer
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }

    const target = targetRef.current;
    if (cursorRef.current >= target.length) {
      setIsTyping(false);
      onCompleteRef.current?.();
      return;
    }

    setIsTyping(true);

    timerRef.current = setInterval(() => {
      const tgt = targetRef.current;
      if (cursorRef.current >= tgt.length) {
        if (timerRef.current) clearInterval(timerRef.current);
        timerRef.current = null;
        setIsTyping(false);
        onCompleteRef.current?.();
        return;
      }
      cursorRef.current += 1;
      setDisplayed(tgt.slice(0, cursorRef.current));
    }, speedRef.current);

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [version]); // eslint-disable-line react-hooks/exhaustive-deps

  return { displayed, isTyping, setTarget, fastForward, speedUp, resetSpeed };
}
