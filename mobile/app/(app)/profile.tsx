import React, { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { Body, Bullet, Button, ChipInput, Field, SectionTitle } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { fonts, space } from "../../lib/theme";
import { useTheme } from "../../lib/theme-context";

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

  const gaps =
    user?.profile_refinement?.known_gaps ||
    profile.profile_refinement?.known_gaps ||
    [];

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
      const p = res.user?.profile || {};
      setHeadline(p.headline || "");
      setLocation(p.location || "");
      setHobbies(p.hobbies || []);
      setInterests(p.interests || []);
      setSports(p.sports || []);
      setMsg("Re-researched your public profile.");
    } catch (e: any) {
      setMsg(e.message || "Research failed");
    } finally {
      setResearching(false);
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

  const syncCalendar = async () => {
    setCalendarBusy(true);
    setMsg("");
    try {
      const res = await api.calendarSyncPrep();
      if (res.status === "skipped") {
        setMsg(res.reason || "Link Google Calendar first (needs GOOGLE_CLIENT_ID).");
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
          {profile.summary_blurb ? <Body>{profile.summary_blurb}</Body> : null}
          <Body>
            At signup we research your public footprint. Dark mode and calendar auto-prep live here.
          </Body>

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

          {(profile.career_highlights || []).length ? (
            <View style={{ marginTop: space.md }}>
              <Text style={{ fontFamily: fonts.bodySemi, color: colors.ember, marginBottom: 8 }}>From research</Text>
              {(profile.career_highlights || []).slice(0, 6).map((c: string) => (
                <Bullet key={c}>{c}</Bullet>
              ))}
            </View>
          ) : null}

          <View style={{ height: space.lg }} />
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
            Link Google Calendar (server keys required) to queue research for upcoming attendees.
          </Body>
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
