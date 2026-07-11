import { useLocalSearchParams, useRouter } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../../components/ScreenBackdrop";
import { Body, Bullet, Button, Field, SectionTitle } from "../../../components/ui";
import { api } from "../../../lib/api";
import { colors, fonts, space } from "../../../lib/theme";

function ListSection({ title, items }: { title: string; items?: any[] }) {
  if (!items || !items.length) return null;
  return (
    <>
      <SectionTitle>{title}</SectionTitle>
      {items.map((item, i) => {
        if (typeof item === "string") return <Bullet key={`${title}-${i}`}>{item}</Bullet>;
        if (item?.name) {
          const line = [item.name, item.title, item.context].filter(Boolean).join(" — ");
          return <Bullet key={`${title}-${i}`}>{line}</Bullet>;
        }
        if (item?.topic) {
          const bits = [item.topic, item.source && `[${item.source}]`, item.snippet || item.evidence]
            .filter(Boolean)
            .join(" · ");
          return <Bullet key={`${title}-${i}`}>{bits}</Bullet>;
        }
        return <Bullet key={`${title}-${i}`}>{JSON.stringify(item)}</Bullet>;
      })}
    </>
  );
}

function Fact({ label, value }: { label: string; value?: any }) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  if (Array.isArray(value)) {
    return (
      <View style={styles.factBlock}>
        <Text style={styles.factLabel}>{label}</Text>
        {value.map((v, i) => (
          <Bullet key={`${label}-${i}`}>{typeof v === "string" ? v : JSON.stringify(v)}</Bullet>
        ))}
      </View>
    );
  }
  return (
    <View style={styles.factBlock}>
      <Text style={styles.factLabel}>{label}</Text>
      <Body>{String(value)}</Body>
    </View>
  );
}

function SocialBlock({ label, data }: { label: string; data?: any }) {
  if (!data || typeof data !== "object") return null;
  const status = data.status;
  if (!status || status === "skipped") return null;
  const profile = data.profile || {};
  const handle = data.handle || profile.username || profile.full_name;
  const url = data.profile_url;
  const posts = data.recent_posts || [];
  return (
    <View style={styles.ground}>
      <Text style={styles.groundTitle}>{label}</Text>
      <Text style={styles.groundSide}>status · {status}{data.match_confidence ? ` · match ${data.match_confidence}` : ""}</Text>
      {handle ? <Text style={styles.groundSide}>@{String(handle).replace(/^@/, "")}</Text> : null}
      {url ? (
        <Text style={styles.link} onPress={() => Linking.openURL(url)}>
          {url}
        </Text>
      ) : null}
      {(profile.biography || profile.bio) ? (
        <Text style={styles.groundSide}>{profile.biography || profile.bio}</Text>
      ) : null}
      {posts.slice(0, 4).map((p: any, i: number) => (
        <Bullet key={i}>{(p.caption || p.snippet || "").slice(0, 160)}</Bullet>
      ))}
    </View>
  );
}

export default function PersonDetail() {
  const { name, company } = useLocalSearchParams<{ name: string; company?: string }>();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!name) return;
    (async () => {
      try {
        const res = await api.person(String(name), company || null);
        setData(res);
      } catch (e: any) {
        setError(e.message || "Failed to load");
      }
    })();
  }, [name, company]);

  if (!data && !error) {
    return (
      <ScreenBackdrop>
        <View style={{ flex: 1, justifyContent: "center" }}>
          <ActivityIndicator color={colors.forest} />
        </View>
      </ScreenBackdrop>
    );
  }

  const summary = data?.summary || {};
  const cgRaw = data?.common_ground;
  const cg =
    cgRaw?.status === "ok"
      ? cgRaw
      : summary.common_ground && !summary.common_ground.status
        ? summary.common_ground
        : cgRaw?.status === "ok"
          ? cgRaw
          : summary.common_ground;
  const starters = summary.conversation_starters || cg?.conversation_starters || [];
  const deep = summary.deep_dive_questions || cg?.deep_dive_questions || [];
  const personal = summary.personal_info || data?.sources?.personal_info || {};
  const presence = summary.public_presence || {};
  const seniors = summary.senior_connections || [];
  const collaborators = summary.research_collaborators || [];
  const contact = data?.contact || {};
  const linkedin =
    contact.linkedin_url ||
    data?.sources?.exa_search?.linkedin_url ||
    data?.sources?.linkedin_public?.profile_url;
  const sources = data?.sources || {};

  const saveNote = async () => {
    if (!note.trim() || !name) return;
    setSaving(true);
    try {
      await api.addNote(String(name), note.trim(), company || null);
      setNote("");
      const res = await api.person(String(name), company || null);
      setData(res);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }} edges={["top"]}>
        <ScrollView contentContainerStyle={styles.pad}>
          <Button title="← Back" variant="ghost" onPress={() => router.back()} style={{ alignSelf: "flex-start", marginBottom: 8 }} />
          <Text style={styles.name}>{data?.name || name}</Text>
          {data?.company ? <Text style={styles.company}>{data.company}</Text> : null}
          {summary.identity_confidence ? (
            <Text style={styles.meta}>
              Identity confidence: {summary.identity_confidence}
              {data?.usage?.tier ? ` · ${data.usage.tier} research` : ""}
            </Text>
          ) : null}
          {error ? <Text style={styles.err}>{error}</Text> : null}

          {cg?.overlap_score != null ? (
            <View style={styles.scoreBox}>
              <Text style={styles.scoreNum}>{cg.overlap_score}</Text>
              <Text style={styles.scoreLabel}>overlap</Text>
            </View>
          ) : null}

          {summary.summary ? (
            <>
              <SectionTitle>Briefing</SectionTitle>
              <Body>{summary.summary}</Body>
            </>
          ) : null}

          {summary.identity_notes ? (
            <>
              <SectionTitle>Identity notes</SectionTitle>
              <Body>{summary.identity_notes}</Body>
            </>
          ) : null}

          <ListSection title="Career" items={summary.career_history} />
          <ListSection title="Interests" items={summary.interests} />
          <ListSection title="Notable points" items={summary.notable_points} />
          <ListSection title="Affiliations" items={summary.notable_affiliations} />
          <ListSection title="Awards & recognition" items={summary.awards_and_recognitions} />

          {collaborators.length ? (
            <>
              <SectionTitle>Collaborators</SectionTitle>
              {collaborators.map((c: any, i: number) => (
                <Bullet key={i}>
                  {c.name}
                  {c.context ? ` — ${c.context}` : ""}
                </Bullet>
              ))}
            </>
          ) : null}

          <SectionTitle>Personal info</SectionTitle>
          <Fact label="Born / hometown" value={personal.born_or_hometown} />
          <Fact label="Birthplace note" value={!personal.born_or_hometown ? personal.birthplace_note : null} />
          <Fact label="Raised in" value={personal.raised_in} />
          <Fact label="Lives now" value={personal.current_location} />
          <Fact label="Also lived in" value={personal.lived_in} />
          <Fact label="Hobbies" value={personal.hobbies} />
          <Fact label="Sports" value={personal.sports_interests} />
          <Fact label="Weekends" value={personal.weekend_preferences} />
          <Fact label="Family" value={personal.family_background} />
          <Fact label="Notes" value={personal.personal_notes} />
          {!personal.born_or_hometown &&
          !personal.raised_in &&
          !personal.current_location &&
          !(personal.hobbies || []).length &&
          !(personal.personal_notes || []).length ? (
            <Body>Nothing public found for personal milestones.</Body>
          ) : null}

          <SectionTitle>Public posts & engagement</SectionTitle>
          <Fact label="Posts about" value={presence.posts_about} />
          {(presence.recent_posts_or_writing || []).length ? (
            <View style={styles.factBlock}>
              <Text style={styles.factLabel}>Recent writing</Text>
              {(presence.recent_posts_or_writing || []).map((item: any, i: number) => (
                <View key={i} style={styles.ground}>
                  <Text style={styles.groundTitle}>{item.topic || "(untitled)"}</Text>
                  {item.source ? <Text style={styles.groundSide}>{item.source}</Text> : null}
                  {item.snippet ? <Text style={styles.groundSide}>{item.snippet}</Text> : null}
                </View>
              ))}
            </View>
          ) : null}
          <ListSection title="Liked / engaged with" items={presence.liked_or_engaged_with} />
          {presence.availability_note ? <Body>{presence.availability_note}</Body> : null}

          {seniors.length ? (
            <>
              <SectionTitle>Senior connections</SectionTitle>
              {seniors.map((p: any, i: number) => (
                <View key={i} style={styles.ground}>
                  <Text style={styles.groundTitle}>
                    {p.name}
                    {p.title ? ` — ${p.title}` : ""}
                    {p.seniority ? ` (${p.seniority})` : ""}
                  </Text>
                  {p.context ? <Text style={styles.groundSide}>{p.context}</Text> : null}
                </View>
              ))}
            </>
          ) : null}

          {(sources.instagram_public || sources.facebook_public || sources.twitter_public) && (
            <>
              <SectionTitle>Social profiles</SectionTitle>
              <SocialBlock label="Instagram" data={sources.instagram_public} />
              <SocialBlock label="Facebook" data={sources.facebook_public} />
              <SocialBlock label="Twitter / X" data={sources.twitter_public} />
            </>
          )}

          {cg && (cg.overlap_summary || (cg.common_grounds || []).length) ? (
            <>
              <SectionTitle>Common ground</SectionTitle>
              {cg.overlap_summary ? <Body>{cg.overlap_summary}</Body> : null}
              {(cg.common_grounds || []).map((g: any, i: number) => (
                <View key={i} style={styles.ground}>
                  <Text style={styles.groundTitle}>
                    {g.point}
                    {g.strength ? ` · ${g.strength}` : ""}
                  </Text>
                  {g.you_side ? <Text style={styles.groundSide}>you · {g.you_side}</Text> : null}
                  {g.them_side ? <Text style={styles.groundSide}>them · {g.them_side}</Text> : null}
                  {g.why_it_matters ? <Text style={styles.groundSide}>why · {g.why_it_matters}</Text> : null}
                </View>
              ))}
              <ListSection title="Related topics" items={cg.related_topics_to_discuss} />
              <ListSection title="Improve your profile" items={cg.your_profile_gaps} />
            </>
          ) : null}

          {starters.length ? (
            <>
              <SectionTitle>Icebreakers</SectionTitle>
              {starters.map((s: string) => (
                <Bullet key={s}>{s}</Bullet>
              ))}
            </>
          ) : null}

          {deep.length ? (
            <>
              <SectionTitle>Deep dives</SectionTitle>
              {deep.map((s: string) => (
                <Bullet key={s}>{s}</Bullet>
              ))}
            </>
          ) : null}

          <SectionTitle>Contact</SectionTitle>
          <Fact label="LinkedIn" value={linkedin} />
          <Fact label="Email" value={contact.email} />
          <Fact label="Phone" value={contact.phone} />
          <Fact label="GitHub" value={contact.github_username} />
          {linkedin ? (
            <Button
              title="Open LinkedIn"
              variant="ember"
              onPress={() => Linking.openURL(linkedin)}
              style={{ marginTop: 10 }}
            />
          ) : (
            <Body>No LinkedIn URL found yet.</Body>
          )}
          {cg?.outreach_angle ? (
            <Text style={styles.outreach}>Suggested note: {cg.outreach_angle}</Text>
          ) : null}

          <SectionTitle>CRM note</SectionTitle>
          <Field label="Note" value={note} onChangeText={setNote} placeholder="Met at TiE, follow up next week…" />
          <Button title="Save note" onPress={saveNote} loading={saving} />

          {(data?.interactions || []).filter((x: any) => x.type === "note").length ? (
            <View style={{ marginTop: space.md }}>
              <Text style={styles.factLabel}>Saved notes</Text>
              {(data.interactions || [])
                .filter((x: any) => x.type === "note")
                .slice()
                .reverse()
                .map((x: any, i: number) => (
                  <Bullet key={i}>
                    {x.note}
                    {x.at ? ` (${String(x.at).slice(0, 10)})` : ""}
                  </Bullet>
                ))}
            </View>
          ) : null}
        </ScrollView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 56 },
  name: { fontFamily: fonts.display, fontSize: 34, color: colors.ink, letterSpacing: -0.6 },
  company: { fontFamily: fonts.body, color: colors.muted, marginBottom: 4 },
  meta: { fontFamily: fonts.body, color: colors.leaf, marginBottom: space.md, fontSize: 13 },
  err: { color: colors.danger, fontFamily: fonts.bodyMed, marginBottom: 8 },
  scoreBox: {
    alignSelf: "flex-start",
    backgroundColor: colors.mist,
    borderRadius: 18,
    paddingHorizontal: 16,
    paddingVertical: 10,
    marginBottom: space.md,
    flexDirection: "row",
    alignItems: "baseline",
    gap: 8,
  },
  scoreNum: { fontFamily: fonts.display, fontSize: 28, color: colors.ember },
  scoreLabel: { fontFamily: fonts.bodyMed, color: colors.moss, fontSize: 13 },
  factBlock: { marginBottom: 10 },
  factLabel: {
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    color: colors.moss,
    textTransform: "uppercase",
    letterSpacing: 0.7,
    marginBottom: 4,
  },
  ground: {
    marginTop: 10,
    padding: 12,
    borderRadius: 14,
    backgroundColor: "rgba(251,252,250,0.85)",
    borderWidth: 1,
    borderColor: colors.line,
  },
  groundTitle: { fontFamily: fonts.bodySemi, color: colors.ink, marginBottom: 4 },
  groundSide: { fontFamily: fonts.body, color: colors.muted, fontSize: 13, lineHeight: 18 },
  link: { fontFamily: fonts.bodyMed, color: colors.ember, fontSize: 13, marginTop: 4 },
  outreach: {
    marginTop: 12,
    fontFamily: fonts.body,
    fontStyle: "italic",
    color: colors.ink,
    lineHeight: 21,
  },
});
