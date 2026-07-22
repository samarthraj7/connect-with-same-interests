import * as Linking from "expo-linking";
import React, { useState } from "react";
import { Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ResearchBriefingPreview } from "../../components/ResearchBriefingPreview";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { Body, Bullet, Button, ChipInput, Field, SectionTitle, UrlLink } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { fonts, space } from "../../lib/theme";
import { useTheme } from "../../lib/theme-context";

function ListSection({ title, items, colors }: { title: string; items?: any[]; colors: any }) {
  if (!items || !items.length) return null;
  return (
    <View style={{ marginTop: space.md }}>
      <Text style={{ fontFamily: fonts.bodySemi, color: colors.ember, marginBottom: 8 }}>{title}</Text>
      {items.map((item, i) => {
        if (typeof item === "string") return <Bullet key={`${title}-${i}`}>{item}</Bullet>;
        if (item?.name) {
          const line = [item.name, item.title, item.context].filter(Boolean).join(" — ");
          return <Bullet key={`${title}-${i}`}>{line}</Bullet>;
        }
        if (item?.topic) {
          const source = item.source ? String(item.source) : "";
          const sourceIsUrl = /^https?:\/\//i.test(source);
          const bits = [item.topic, !sourceIsUrl && source && `[${source}]`, item.snippet || item.evidence]
            .filter(Boolean)
            .join(" · ");
          return (
            <View key={`${title}-${i}`}>
              <Bullet>{bits}</Bullet>
              {sourceIsUrl ? (
                <View style={{ marginLeft: 16, marginTop: -4 }}>
                  <UrlLink url={source} label="source" />
                </View>
              ) : null}
            </View>
          );
        }
        if (item?.fact) {
          const hint = item.source_hint ? String(item.source_hint) : "";
          const hintIsUrl = /^https?:\/\//i.test(hint);
          return (
            <View key={`${title}-${i}`} style={{ marginBottom: 8 }}>
              <Bullet>{String(item.fact)}</Bullet>
              {hintIsUrl ? (
                <View style={{ marginLeft: 16, marginTop: -4 }}>
                  <UrlLink url={hint} label="source" />
                </View>
              ) : hint ? (
                <Text style={{ marginLeft: 16, fontFamily: fonts.body, color: colors.muted, fontSize: 13 }}>
                  {hint}
                </Text>
              ) : null}
            </View>
          );
        }
        return <Bullet key={`${title}-${i}`}>{JSON.stringify(item)}</Bullet>;
      })}
    </View>
  );
}

function Fact({ label, value, colors }: { label: string; value?: any; colors: any }) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  if (Array.isArray(value)) {
    return (
      <View style={{ marginBottom: 10 }}>
        <Text
          style={{
            fontFamily: fonts.bodyMed,
            fontSize: 12,
            color: colors.moss,
            textTransform: "uppercase",
            letterSpacing: 0.7,
            marginBottom: 4,
          }}
        >
          {label}
        </Text>
        {value.map((v, i) => (
          <Bullet key={`${label}-${i}`}>{typeof v === "string" ? v : JSON.stringify(v)}</Bullet>
        ))}
      </View>
    );
  }
  const str = String(value);
  const looksLikeUrl = /^https?:\/\//i.test(str.trim());
  return (
    <View style={{ marginBottom: 10 }}>
      <Text
        style={{
          fontFamily: fonts.bodyMed,
          fontSize: 12,
          color: colors.moss,
          textTransform: "uppercase",
          letterSpacing: 0.7,
          marginBottom: 4,
        }}
      >
        {label}
      </Text>
      {looksLikeUrl ? <UrlLink url={str.trim()} label={`Open ${label}`} /> : <Body>{str}</Body>}
    </View>
  );
}

function applySummaryToFields(
  summary: Record<string, any> | undefined,
  setters: {
    setHeadline: (v: string) => void;
    setLocation: (v: string) => void;
    setHobbies: (v: string[]) => void;
    setInterests: (v: string[]) => void;
    setSports: (v: string[]) => void;
  }
) {
  if (!summary) return;
  const personal = summary.personal_info || {};
  const career0 = Array.isArray(summary.career_history) ? summary.career_history[0] : "";
  if (typeof career0 === "string" && career0) setters.setHeadline(career0.slice(0, 160));
  if (personal.current_location) setters.setLocation(String(personal.current_location));
  if (Array.isArray(personal.hobbies) && personal.hobbies.length) setters.setHobbies(personal.hobbies);
  if (Array.isArray(summary.interests) && summary.interests.length) {
    setters.setInterests(summary.interests.map((x: any) => String(x)).slice(0, 12));
  }
  if (Array.isArray(personal.sports_interests) && personal.sports_interests.length) {
    setters.setSports(personal.sports_interests.map((x: any) => String(x)));
  }
}

export default function ProfileScreen() {
  const { user, refresh, signOut } = useAuth();
  const { colors, mode, toggle } = useTheme();
  const profile = user?.profile || {};
  const [headline, setHeadline] = useState(profile.headline || "");
  const [location, setLocation] = useState(profile.location || "");
  const [hobbies, setHobbies] = useState<string[]>(profile.hobbies || []);
  const [interests, setInterests] = useState<string[]>(profile.interests || []);
  const [sports, setSports] = useState<string[]>(profile.sports || []);
  const [saving, setSaving] = useState(false);
  const [researching, setResearching] = useState(false);
  const [msg, setMsg] = useState("");
  const [calendarBusy, setCalendarBusy] = useState(false);
  const [journal, setJournal] = useState<any[]>([]);
  const [journalDraft, setJournalDraft] = useState("");
  const [draftId, setDraftId] = useState<string | null>(profile.research_draft_id || null);
  const [draftSummary, setDraftSummary] = useState<string>("");
  const [draftHighlights, setDraftHighlights] = useState<string[]>([]);
  const [draftBriefing, setDraftBriefing] = useState<Record<string, any> | null>(null);
  const [ratingNotes, setRatingNotes] = useState("");
  const [ratingBusy, setRatingBusy] = useState(false);

  const gaps =
    user?.profile_refinement?.known_gaps ||
    profile.profile_refinement?.known_gaps ||
    [];

  const fieldSetters = { setHeadline, setLocation, setHobbies, setInterests, setSports };

  const ingestDraftPreview = (summary: Record<string, any> | undefined, id?: string | null) => {
    if (id) setDraftId(id);
    setDraftBriefing(summary || null);
    const blurb = typeof summary?.summary === "string" ? summary.summary : "";
    setDraftSummary(blurb);
    const career = Array.isArray(summary?.career_history)
      ? summary!.career_history.map((c: any) => String(c)).slice(0, 6)
      : [];
    setDraftHighlights(career);
    applySummaryToFields(summary, fieldSetters);
  };

  React.useEffect(() => {
    const pendingId = profile.research_draft_id || draftId;
    if (profile.research_status !== "pending_rating" || !pendingId) return;
    let cancelled = false;
    api
      .researchDraft(pendingId)
      .then((d) => {
        if (cancelled) return;
        ingestDraftPreview(d.summary, pendingId);
        setMsg("Research is ready — rate it Good to save on your profile.");
      })
      .catch(() => {
        if (!cancelled) setDraftId(null);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, profile.research_status, profile.research_draft_id]);

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      await api.updateProfile({ headline, location, hobbies, interests, sports });
      await api.updateSettings({ theme: mode });
      await refresh();
      setMsg("Saved — better conversation ideas on your next detailed research.");
    } catch (e: any) {
      setMsg(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const reResearch = async () => {
    setResearching(true);
    setMsg("");
    try {
      const res = await api.researchMe({ force_refresh: true });
      await refresh();
      if (res.needs_rating && res.draft_id) {
        ingestDraftPreview(res.summary, res.draft_id);
        setMsg("Research draft ready — tap Good to populate your profile (or Bad to discard).");
        return;
      }
      const p = res.user?.profile || {};
      setHeadline(p.headline || "");
      setLocation(p.location || "");
      setHobbies(p.hobbies || []);
      setInterests(p.interests || []);
      setSports(p.sports || []);
      setDraftId(null);
      setDraftSummary("");
      setDraftHighlights([]);
      setDraftBriefing(null);
      setMsg("Re-researched your public profile.");
    } catch (e: any) {
      setMsg(e.message || "Research failed");
    } finally {
      setResearching(false);
    }
  };

  const rateDraft = async (rating: "good" | "bad") => {
    if (!draftId) return;
    if (rating === "bad" && !ratingNotes.trim()) {
      setMsg("Tell us what was wrong so re-research can fix it.");
      return;
    }
    setRatingBusy(true);
    setMsg(rating === "bad" ? "Saving notes and re-researching…" : "");
    try {
      const res = await api.researchFeedback({
        draft_id: draftId,
        rating,
        wrong_notes: rating === "bad" ? ratingNotes.trim() || undefined : undefined,
        wrong_categories: rating === "bad" ? ["self_research"] : undefined,
        auto_retry: rating === "bad",
      });
      setRatingNotes("");
      await refresh();
      if (rating === "bad") {
        if (res.retried && res.draft_id) {
          ingestDraftPreview(res.summary, res.draft_id);
          setMsg(res.message || "New draft ready — review and rate again.");
          return;
        }
        setDraftId(null);
        setDraftSummary("");
        setDraftHighlights([]);
        setDraftBriefing(null);
        setMsg(res.message || "Draft discarded. Re-research when you want to try again.");
        return;
      }
      setDraftId(null);
      setDraftSummary("");
      setDraftHighlights([]);
      setDraftBriefing(null);
      const p = res.user?.profile || {};
      setHeadline(p.headline || headline);
      setLocation(p.location || location);
      setHobbies(p.hobbies || hobbies);
      setInterests(p.interests || interests);
      setSports(p.sports || sports);
      setMsg("Saved research to your profile.");
    } catch (e: any) {
      setMsg(e.message || "Could not submit rating");
    } finally {
      setRatingBusy(false);
    }
  };

  React.useEffect(() => {
    api.privateJournal().then((r) => setJournal(r.entries || [])).catch(() => undefined);
  }, [user?.id]);

  const saveJournal = async () => {
    if (!journalDraft.trim()) return;
    setMsg("");
    try {
      const res = await api.addPrivateJournal({ body: journalDraft.trim(), entry_type: "blog" });
      setJournal((res as any).entries || []);
      setJournalDraft("");
      setMsg("Private note saved — used for overlap only, never public.");
    } catch (e: any) {
      setMsg(e.message || "Journal save failed");
    }
  };

  const linkCalendar = async () => {
    setCalendarBusy(true);
    setMsg("");
    try {
      const redirectUri =
        Platform.OS === "web" && typeof window !== "undefined"
          ? `${window.location.origin}/calendar-oauth`
          : Linking.createURL("calendar-oauth");
      const res = await api.calendarOAuthUrl(redirectUri);
      if (res.status === "skipped" || !res.url) {
        setMsg(
          res.reason ||
            "Google Calendar isn’t configured on the server yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to backend/.env, then restart the API.",
        );
        return;
      }
      const canOpen = await Linking.canOpenURL(res.url);
      if (!canOpen) {
        setMsg("Could not open Google sign-in in this environment.");
        return;
      }
      await Linking.openURL(res.url);
      setMsg(
        "Complete Google sign-in in the browser. After you approve access, return here and tap Sync upcoming meetings. (Register this redirect URI in Google Cloud: " +
          redirectUri +
          ")",
      );
    } catch (e: any) {
      setMsg(e.message || "Could not start Google Calendar link");
    } finally {
      setCalendarBusy(false);
    }
  };

  // Capture OAuth redirect on web/deep link when ?code= is present
  React.useEffect(() => {
    const handleUrl = async (url: string) => {
      try {
        const parsed = Linking.parse(url);
        const code = (parsed.queryParams?.code as string) || "";
        if (!code) return;
        const redirectUri =
          Platform.OS === "web" && typeof window !== "undefined"
            ? `${window.location.origin}/calendar-oauth`
            : Linking.createURL("calendar-oauth");
        setCalendarBusy(true);
        await api.calendarOAuth({ code, redirect_uri: redirectUri });
        setMsg("Google Calendar linked. Tap Sync upcoming meetings.");
        await refresh();
      } catch (e: any) {
        setMsg(e.message || "Calendar OAuth failed");
      } finally {
        setCalendarBusy(false);
      }
    };
    const sub = Linking.addEventListener("url", ({ url }) => {
      handleUrl(url);
    });
    Linking.getInitialURL().then((url) => {
      if (url) handleUrl(url);
    });
    return () => sub.remove();
  }, [refresh]);

  const syncCalendar = async () => {
    setCalendarBusy(true);
    setMsg("");
    try {
      const res = await api.calendarSyncPrep();
      if (res.status === "skipped") {
        setMsg(res.reason || "Link Google Calendar first.");
      } else {
        setMsg(`Calendar prep: added ${res.added ?? 0} attendees to queue.`);
      }
      await refresh();
    } catch (e: any) {
      setMsg(e.message || "Calendar sync failed");
    } finally {
      setCalendarBusy(false);
    }
  };

  const awaitingRating = Boolean(draftId);
  const briefing = draftBriefing || profile.latest_summary || null;
  const personal = briefing?.personal_info || profile.personal_info || {};
  const presence = briefing?.public_presence || profile.public_presence || {};
  const seniors = briefing?.senior_connections || profile.senior_connections || [];
  const collaborators = briefing?.research_collaborators || profile.research_collaborators || [];
  const linkedin =
    (profile.contact || {}).linkedin_url ||
    (user?.profile?.contact || {}).linkedin_url ||
    "";

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }} edges={["top"]}>
        <ScrollView contentContainerStyle={styles.pad}>
          <SectionTitle>You</SectionTitle>
          <Text style={[styles.name, { color: colors.ink }]}>{profile.name || user?.email}</Text>
          <Text style={{ fontFamily: fonts.bodyMed, color: colors.leaf, marginBottom: 4 }}>
            {user?.tokens ?? 0} tokens remaining
          </Text>
          <Text style={{ fontFamily: fonts.body, color: colors.muted, fontSize: 13, marginBottom: space.md }}>
            Profile source: {profile.profile_source || user?.profile_source || "manual"}
            {profile.research_status ? ` · research ${profile.research_status}` : ""}
            {user?.connections_count ? ` · ${user.connections_count} connections imported` : ""}
          </Text>
          {profile.summary_blurb || briefing?.summary ? (
            <Body>{profile.summary_blurb || briefing?.summary}</Body>
          ) : null}
          {profile.identity_confidence || briefing?.identity_confidence ? (
            <Text style={{ fontFamily: fonts.body, color: colors.leaf, fontSize: 13, marginBottom: 8 }}>
              Identity: {profile.identity_confidence || briefing?.identity_confidence}
            </Text>
          ) : null}

          {awaitingRating ? (
            <View
              style={{
                marginVertical: space.md,
                padding: 14,
                borderRadius: 16,
                borderWidth: 1,
                borderColor: colors.line,
                backgroundColor: colors.mist,
                gap: 10,
              }}
            >
              <Text style={{ fontFamily: fonts.bodySemi, color: colors.ember }}>
                Review the full research before saving
              </Text>
              <ResearchBriefingPreview
                summary={draftBriefing}
                name={profile.name}
                company={profile.current_company}
                linkedinUrl={linkedin}
              />
              {draftHighlights.map((c) => (
                <Bullet key={c}>{c}</Bullet>
              ))}
              <Field
                label="What was wrong? (required for Fix & re-research)"
                value={ratingNotes}
                onChangeText={setRatingNotes}
                multiline
                placeholder="Wrong person, wrong company, outdated role…"
              />
              <View style={{ flexDirection: "row", gap: 10 }}>
                <View style={{ flex: 1 }}>
                  <Button title="Good — save" onPress={() => rateDraft("good")} loading={ratingBusy} />
                </View>
                <View style={{ flex: 1 }}>
                  <Button
                    title="Bad — fix & re-research"
                    variant="ghost"
                    onPress={() => rateDraft("bad")}
                    loading={ratingBusy}
                  />
                </View>
              </View>
            </View>
          ) : null}

          <View style={{ marginVertical: space.md, gap: 10 }}>
            <Button
              title={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              variant="ghost"
              onPress={() => {
                toggle();
                api.updateSettings({ theme: mode === "dark" ? "light" : "dark" }).catch(() => undefined);
              }}
            />
          </View>

          <SectionTitle>Reach out</SectionTitle>
          {linkedin ? (
            <>
              <Fact label="LinkedIn" value={linkedin} colors={colors} />
            </>
          ) : (
            <Body>No LinkedIn URL on your profile yet — re-research after picking your LinkedIn match.</Body>
          )}

          <ListSection
            title="Career"
            items={briefing?.career_history || profile.career_highlights}
            colors={colors}
          />
          <ListSection
            title="Notable"
            items={briefing?.notable_points || profile.notable_points}
            colors={colors}
          />
          <ListSection
            title="Affiliations"
            items={briefing?.notable_affiliations || profile.causes_and_affiliations}
            colors={colors}
          />
          <ListSection
            title="Awards"
            items={briefing?.awards_and_recognitions || profile.awards_and_recognitions}
            colors={colors}
          />

          {(personal.born_or_hometown ||
            personal.raised_in ||
            personal.current_location ||
            (personal.hobbies || []).length ||
            (personal.sports_interests || []).length ||
            (personal.family_background || []).length ||
            (personal.personal_notes || []).length) && (
            <>
              <SectionTitle>Personal</SectionTitle>
              <Fact label="Born / hometown" value={personal.born_or_hometown} colors={colors} />
              <Fact label="Raised in" value={personal.raised_in} colors={colors} />
              <Fact label="Current location" value={personal.current_location || profile.location} colors={colors} />
              <Fact label="Lived in" value={personal.lived_in || profile.lived_in} colors={colors} />
              <Fact label="Hobbies" value={personal.hobbies || profile.hobbies} colors={colors} />
              <Fact label="Sports" value={personal.sports_interests || profile.sports} colors={colors} />
              <Fact label="Weekends" value={personal.weekend_preferences} colors={colors} />
              <Fact label="Family" value={personal.family_background} colors={colors} />
              <Fact label="Notes" value={personal.personal_notes} colors={colors} />
              <ListSection title="Evidence" items={personal.evidence} colors={colors} />
            </>
          )}

          {(presence.posts_about?.length ||
            presence.recent_posts_or_writing?.length ||
            presence.availability_note) && (
            <>
              <SectionTitle>Public presence</SectionTitle>
              <ListSection title="Themes" items={presence.posts_about} colors={colors} />
              <ListSection title="Recent writing" items={presence.recent_posts_or_writing} colors={colors} />
              <Fact label="Availability" value={presence.availability_note} colors={colors} />
            </>
          )}

          <ListSection title="Senior connections" items={seniors} colors={colors} />
          <ListSection title="Collaborators" items={collaborators} colors={colors} />

          {(profile.education || []).length ? (
            <ListSection title="Education" items={profile.education} colors={colors} />
          ) : null}

          <View style={{ height: space.lg }} />
          <SectionTitle>Edit profile</SectionTitle>
          <Field label="Headline" value={headline} onChangeText={setHeadline} />
          <Field label="Location" value={location} onChangeText={setLocation} />
          <ChipInput label="Hobbies" value={hobbies} onChange={setHobbies} />
          <ChipInput label="Interests" value={interests} onChange={setInterests} />
          <ChipInput label="Sports" value={sports} onChange={setSports} />

          <SectionTitle>Private journal</SectionTitle>
          <Body>
            Daily notes and blogs stay private. They fuel overlap when someone else researches you — they never
            appear on your public dossier.
          </Body>
          <Field label="New private note" value={journalDraft} onChangeText={setJournalDraft} multiline />
          <Button title="Add private note" onPress={saveJournal} style={{ marginBottom: 10 }} />
          {journal.slice(0, 8).map((e) => (
            <Bullet key={e.id}>
              [{e.type}] {e.body}
            </Bullet>
          ))}

          {gaps.length ? (
            <View
              style={{
                marginVertical: space.md,
                padding: 14,
                borderRadius: 16,
                backgroundColor: "rgba(196, 92, 38, 0.08)",
              }}
            >
              <Text style={{ fontFamily: fonts.bodySemi, color: colors.ember, marginBottom: 8 }}>
                Suggested refinements
              </Text>
              {gaps.map((g: string) => (
                <Bullet key={g}>{g}</Bullet>
              ))}
            </View>
          ) : null}

          {msg ? (
            <Text style={{ fontFamily: fonts.body, color: colors.moss, marginBottom: 10 }}>{msg}</Text>
          ) : null}
          <Button title="Save profile" onPress={save} loading={saving} />
          <Button
            title="Re-research my public profile"
            variant="ember"
            onPress={reResearch}
            loading={researching}
            style={{ marginTop: 10 }}
          />
          <SectionTitle>Calendar auto-prep</SectionTitle>
          <Body>
            Link Google Calendar to queue research for upcoming meeting attendees. Requires GOOGLE_CLIENT_ID /
            GOOGLE_CLIENT_SECRET on the API.
          </Body>
          <Button
            title="Link Google Calendar"
            variant="ember"
            onPress={linkCalendar}
            loading={calendarBusy}
            style={{ marginTop: 10 }}
          />
          <Button
            title="Sync upcoming meetings"
            variant="ghost"
            onPress={syncCalendar}
            loading={calendarBusy}
            style={{ marginTop: 10 }}
          />
          <Button title="Sign out" variant="ghost" onPress={signOut} style={{ marginTop: 10 }} />
        </ScrollView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 48 },
  name: { fontFamily: fonts.display, fontSize: 28, marginBottom: 4 },
});
