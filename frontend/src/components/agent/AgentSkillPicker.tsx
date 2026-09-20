/**
 * 技能选择器：把后端下发的技能渲染为可切换的 chip，用于「种子化」下一条消息。
 */

import { Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AgentSkill } from '@/types/agent';

export interface AgentSkillPickerProps {
  skills: AgentSkill[];
  selected: string | null;
  onSelect: (name: string | null) => void;
}

export function AgentSkillPicker({ skills, selected, onSelect }: AgentSkillPickerProps) {
  if (skills.length === 0) return null;

  return (
    <div className="shrink-0 space-y-1">
      <div className="text-muted-foreground flex items-center gap-1 text-[11px]">
        <Sparkles className="size-3" aria-hidden="true" />
        技能
      </div>
      <div className="flex flex-wrap gap-1.5">
        {skills.map((skill) => {
          const active = skill.name === selected;
          return (
            <button
              key={skill.name}
              type="button"
              title={skill.description}
              aria-pressed={active}
              onClick={() => onSelect(active ? null : skill.name)}
              className={cn(
                'rounded-full border px-2.5 py-0.5 text-xs transition-colors',
                active
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-input hover:bg-accent',
              )}
            >
              {skill.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}
