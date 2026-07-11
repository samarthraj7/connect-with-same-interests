import React, { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { Body, Bullet, Button, ChipInput, Field, SectionTitle } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors, fonts, space } from "../../lib/theme";

export default function ProfileScreen() {
  const { user, refresh, signOut } = useAuth();
  const profile = user?.profile || {};
  const [headline, setHeadline] = useState(profile.headline || "");
  const [location, setLocation] = useState(profile.location || "");
  const [hobbies, setHobbies] = useState<string[]>(profile.hobbies || []);
  const [interests, setInterests] = useState<string[]>(profile.interests || []);
  const [sports, setSports] = useState<string[]>(profile.sports || []);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const gaps =
    user?.profile_refinement?.known_gaps ||
    profile.profile_refinement?.known_gaps ||
    [];

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      await api.updateProfile({ headline, location, hobbies, interests, sports });
      await refresh();
      setMsg("Saved — better overlap on your next detailed research.");
    } catch (e: any) {
      setMsg(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }} edges={["top"]}>
        <ScrollView contentContainerStyle={styles.pad}>
          <SectionTitle>You</SectionTitle>
          <Text style={styles.name}>{profile.name || user?.email}</Text>
          <Text style={styles.tokens}>{user?.tokens ?? 0} tokens remaining</Text>
          <Body>
            Hobbies and interests here are your social signal for common ground. When someone else
            signs up, theirs will work the same way — no scraping required.
          </Body>

          <View style={{ height: space.lg }} />
          <Field label="Headline" value={headline} onChangeText={setHeadline} />
          <Field label="Location" value={location} onChangeText={setLocation} />
          <ChipInput label="Hobbies" value={hobbies} onChange={setHobbies} />
          <ChipInput label="Interests" value={interests} onChange={setInterests} />
          <ChipInput label="Sports" value={sports} onChange={setSports} />

          {gaps.length ? (
            <View style={styles.gapBox}>
              <Text style={styles.gapTitle}>Suggested refinements</Text>
              {gaps.map((g: string) => (
                <Bullet key={g}>{g}</Bullet>
              ))}
            </View>
          ) : null}

          {msg ? <Text style={styles.msg}>{msg}</Text> : null}
          <Button title="Save profile" onPress={save} loading={saving} />
          <Button title="Sign out" variant="ghost" onPress={signOut} style={{ marginTop: 10 }} />
        </ScrollView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 48 },
  name: { fontFamily: fonts.display, fontSize: 28, color: colors.ink, marginBottom: 4 },
  tokens: { fontFamily: fonts.bodyMed, color: colors.leaf, marginBottom: space.md },
  gapBox: {
    marginVertical: space.md,
    padding: 14,
    borderRadius: 16,
    backgroundColor: "rgba(196, 92, 38, 0.08)",
  },
  gapTitle: { fontFamily: fonts.bodySemi, color: colors.ember, marginBottom: 8 },
  msg: { fontFamily: fonts.body, color: colors.moss, marginBottom: 10 },
});
