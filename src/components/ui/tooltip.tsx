"use client";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";
export function Tooltip({ children, content }: { children: ReactNode; content: string }) { return <TooltipPrimitive.Provider delayDuration={200}><TooltipPrimitive.Root><TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger><TooltipPrimitive.Portal><TooltipPrimitive.Content sideOffset={6} className="z-50 max-w-64 rounded-lg bg-navy px-3 py-2 text-xs leading-relaxed text-white shadow-md">{content}<TooltipPrimitive.Arrow className="fill-navy" /></TooltipPrimitive.Content></TooltipPrimitive.Portal></TooltipPrimitive.Root></TooltipPrimitive.Provider>; }
