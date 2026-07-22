import React from "react";
import { Text, View } from "react-native";
import { fonts, space } from "../lib/theme";
import { useTheme } from "../lib/theme-context";
import { Body } from "./ui";

function asList(items: any): string[] {
  if (!Array.isArray(items)) return [];
  return items
    .map((x) => {
      if (typeof x === "string") return x;
      if (x && typeof x === "object") {
        return x.topic || x.name || x.title || x.fact || x.snippet || JSON.stringify(x);
      }
      return String(x ?? "");
    })
    .map((s) => s.trim())
    .filter(Boolean);
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const { colors } = useTheme();
  if (!children) return null;
  return (
    <View style={{ marginBottom: space.md }}>
      <Text
        style={{
          fontFamily: fonts.bodyMed,
          fontSize: 12,
          color: colors.moss,
          letterSpacing: 0.8,
          textTransform: "uppercase",
          marginBottom: 6,
        }}
      >
        {title}
      </Text>
      {children}
    </View>
  );
}

function Bullets({ items }: { items: string[] }) {
  const { colors } = useTheme();
  if (!items.length) return null;
  return (
    <View style={{ gap: 4 }}>
      {items.slice(0, 8).map((item) => (
        <Text key={item} style={{ fontFamily: fonts.body, color: colors.ink, lineHeight: 20 }}>
          · {item}
        </Text>
      ))}
    </View>
  );
}

/** Compact read-only dossier for signup / rating review. */
export function ResearchBriefingPreview({
  summary,
  name,
  company,
  linkedinUrl,
}: {
  summary?: Record<string, any> | null;
  name?: string;
  company?: string | null;
  linkedinUrl?: string | null;
}) {
  const { colors } = useTheme();
  if (!summary && !name) return null;
  const s = summary || {};
  const personal = s.personal_info || {};
  const presence = s.public_presence || {};
  const role = [s.current_role, s.current_company || company].filter(Boolean).join(" · ");

  return (
    <View
      style={{
        borderWidth: 1,
        borderColor: colors.line,
        borderRadius: 16,
        padding: 14,
        backgroundColor: colors.mist,
        marginBottom: 12,
        gap: 4,
      }}
    >
      {name ? (
        <Text style={{ fontFamily: fonts.bodySemi, color: colors.ink, fontSize: 18 }}>{name}</Text>
      ) : null}
      {role ? (
        <Text style={{ fontFamily: fonts.body, color: colors.moss, marginBottom: 6 }}>{role}</Text>
      ) : null}
      {linkedinUrl ? (
        <Text style={{ fontFamily: fonts.body, color: colors.leaf, fontSize: 13, marginBottom: 8 }}>
          {linkedinUrl}
        </Text>
      ) : null}
      {s.summary ? (
        <Section title="Summary">
          <Body>{s.summary}</Body>
        </Section>
      ) : null}
      <Section title="Career">
        <Bullets items={asList(s.career_history)} />
      </Section>
      <Section title="Notable">
        <Bullets items={asList(s.notable_points)} />
      </Section>
      <Section title="Interests">
        <Bullets items={asList(s.interests)} />
      </Section>
      <Section title="Affiliations">
        <Bullets items={asList(s.notable_affiliations)} />
      </Section>
      <Section title="Personal">
        <Bullets
          items={[
            personal.born_or_hometown ? `Hometown: ${personal.born_or_hometown}` : "",
            personal.raised_in ? `Raised: ${personal.raised_in}` : "",
            personal.current_location ? `Location: ${personal.current_location}` : "",
            ...asList(personal.hobbies).map((h) => `Hobby: ${h}`),
            ...asList(personal.sports_interests).map((h) => `Sport: ${h}`),
            ...asList(personal.personal_notes),
          ].filter(Boolean)}
        />
      </Section>
      <Section title="Public presence">
        <Bullets items={[...asList(presence.posts_about), ...asList(presence.recent_posts_or_writing)]} />
      </Section>
      {s.identity_confidence ? (
        <Text style={{ fontFamily: fonts.body, color: colors.muted, fontSize: 12, marginTop: 4 }}>
          Identity confidence: {s.identity_confidence}
          {s.identity_notes ? ` — ${s.identity_notes}` : ""}
        </Text>
      ) : null}
    </View>
  );
}
