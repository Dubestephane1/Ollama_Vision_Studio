import React, { useState } from "react";
import { X, Wrench, Play, CheckCircle2, AlertCircle, ShieldAlert } from "lucide-react";
import type { Skill } from "../types";
import { api } from "../lib/api";
import { useToast } from "./Toaster";

interface SkillsModalProps {
  isOpen: boolean;
  onClose: () => void;
  skills: Skill[];
  activeSkills: string[];
  onToggleSkill: (skillName: string) => void;
}

export const SkillsModal: React.FC<SkillsModalProps> = ({
  isOpen,
  onClose,
  skills,
  activeSkills,
  onToggleSkill,
}) => {
  const [testResult, setTestResult] = useState<{ skill: string; data: any } | null>(null);
  const [isRunning, setIsRunning] = useState<string | null>(null);
  const toast = useToast();

  if (!isOpen) return null;

  const runTestSkill = async (skill: Skill) => {
    setIsRunning(skill.name);
    setTestResult(null);

    // Give the skill the args its schema asks for, so the test is meaningful.
    const args: Record<string, any> = {};
    const props = skill.parameters?.properties || {};
    for (const [key, def] of Object.entries(props) as [string, any][]) {
      args[key] = placeholderFor(def, key);
    }

    try {
      const res = await fetch("/api/skills/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_name: skill.name, args }),
      });
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const data = await res.json();
      setTestResult({ skill: skill.name, data: data.result });
    } catch (err: any) {
      setTestResult({ skill: skill.name, data: { error: err.message } });
    } finally {
      setIsRunning(null);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content skills-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <Wrench size={18} color="var(--accent-primary)" />
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700 }}>Tools &amp; Skills</h3>
          </div>
          <button className="action-btn" onClick={onClose} title="Close modal">
            <X size={18} />
          </button>
        </div>

        <p className="skills-intro">
          Enabled tools are offered to the model on every turn. It decides for itself
          which ones to call. Add your own by dropping a{" "}
          <code>BaseSkill</code> subclass into <code>backend/skills/</code>.
        </p>

        <div className="skills-list">
          {skills.length === 0 ? (
            <div className="skills-none">No skills discovered in backend/skills.</div>
          ) : (
            skills.map((skill) => {
              const enabled = activeSkills.includes(skill.name);
              return (
                <div key={skill.name} className={`skill-card ${enabled ? "on" : ""}`}>
                  <div className="skill-info">
                    <div className="skill-title-row">
                      <h4>{skill.display_name}</h4>
                      <code className="skill-name-tag">{skill.name}</code>
                    </div>
                    <p>{skill.description}</p>
                    <div className="skill-test-row">
                      <button
                        className="skill-test-btn"
                        onClick={() => runTestSkill(skill)}
                        disabled={isRunning === skill.name}
                      >
                        <Play size={10} />
                        <span>
                          {isRunning === skill.name ? "Running…" : "Run standalone test"}
                        </span>
                      </button>
                    </div>
                  </div>

                  <label className="toggle-switch">
                    <input
                      type="checkbox"
                      checked={enabled}
                      onChange={() => onToggleSkill(skill.name)}
                    />
                    <span className="slider" />
                  </label>
                </div>
              );
            })
          )}
        </div>

        {testResult && (
          <div className="skill-test-output">
            <div className="skill-test-head">
              {testResult.data?.error ? (
                <AlertCircle size={14} color="var(--danger)" />
              ) : (
                <CheckCircle2 size={14} color="var(--success)" />
              )}
              <span>
                Output from <code>{testResult.skill}</code>
              </span>
            </div>
            <pre>{JSON.stringify(testResult.data, null, 2)}</pre>
          </div>
        )}

        <div className="skills-security-note">
          <ShieldAlert size={13} />
          <span>
            Skills are Python and run locally with your permissions. Only enable tools
            you trust — and review anything you drop into <code>backend/skills/</code>.
          </span>
        </div>
      </div>
    </div>
  );
};

/** Produce a harmless sample value for a skill's parameters. */
function placeholderFor(def: any, key: string): any {
  const name = key.toLowerCase();
  if (def?.enum?.length) return def.enum[0];
  switch (def?.type) {
    case "string":
      if (name.includes("url")) return "https://example.com";
      if (name.includes("expression")) return "2 + 2";
      if (name.includes("image")) return "";
      if (name.includes("query") || name.includes("search")) return "Python programming";
      return "test";
    case "number":
    case "integer":
      return 1;
    case "boolean":
      return false;
    case "array":
      return [];
    case "object":
      return {};
    default:
      return "test";
  }
}