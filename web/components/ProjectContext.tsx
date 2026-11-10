"use client";
import { createContext, useContext } from "react";
import type { Project } from "@/lib/types";

interface Ctx {
  project: Project;
  reload: () => void;
  setProject: (p: Project) => void;
}
export const ProjectCtx = createContext<Ctx | null>(null);
export function useProject(): Ctx {
  const v = useContext(ProjectCtx);
  if (!v) throw new Error("useProject outside project layout");
  return v;
}
