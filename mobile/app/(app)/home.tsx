import { useRouter } from "expo-router";
import React, { useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { BrandMark, Button, Field } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { fonts, space } from "../../lib/theme";
import { useTheme } from "../../lib/theme-context";

type Candidate = {
  name: string;
  role?: string;
  company?: string;
  location?: string;
  linkedin_url?: string;
  photo_url?: string;
};

export default function Home() {
  const { user, refresh } = useAuth();
  const { colors, toggle, mode } = useTheme();
  const router = useRouter();
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [university, setUniversity] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [tier, setTier] = useState<"basic" | "detailed">("detailed");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [researching, setResearching] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [mustPick, setMustPick] = useState(false);

  const hasDisambiguator = () =>
    !!(company.trim() || university.trim() || linkedin.trim());

  const findPeople = async () => {
    setError("");
    setCandidates([]);
    setMustPick(false);
    if (!name.trim()) {
      setError("Enter a full name to search.");
      return;
    }
    setLoadingCandidates(true);
    try {
      const res = await api.candidates(name.trim());
      const list = res.candidates || [];
      setCandidates(list);
      if (list.length > 1) {
        setMustPick(true);
        setStatus("Multiple people match — pick Full name · Company · Role.");
      } else if (list.length === 1) {
        setStatus("One candidate found — tap to confirm, or research with the fields below.");
      } else {
        setStatus("No disambiguation hits — add company or LinkedIn, then research.");
      }
    } catch (e: any) {
      setError(e.message || "Candidate search failed");
    } finally {
      setLoadingCandidates(false);
    }
  };

  const runResearch = async (override?: Partial<Candidate>) => {
    setError("");
    setStatus("");
    const who = override?.name || name.trim();
    if (!who) {
      setError("Full name required.");
      return;
    }
    const co = override?.company || company.trim() || null;
    const li = override?.linkedin_url || linkedin.trim() || null;
    const uni = university.trim() || null;
    if (!co && !uni && !li) {
      setError("Add company, university, or LinkedIn before researching.");
      return;
    }
    if (mustPick && !override && candidates.length > 1) {
      setError("Pick a person from the list (name · company · role).");
      return;
    }
    const cost = tier === "detailed" ? 3 : 1;
    if ((user?.tokens ?? 0) < cost) {
      setError(`Need ${cost} tokens (you have ${user?.tokens ?? 0}).`);
      return;
    }
    setResearching(true);
    setStatus(tier === "detailed" ? "Researching + crafting conversation ideas…" : "Running basic research…");
    try {
      const res = await api.research({
        name: who,
        company: co,
        university: uni,
        linkedin_url: li,
        place: override?.location || null,
        tier,
        fetch_social: false,
      });
      await refresh();
      if (res.status !== "ok") throw new Error(res.error || "Research failed");
      router.push({
        pathname: "/(app)/person/[name]",
        params: {
          name: res.name,
          company: res.company || "",
          ...(li ? { linkedin: li } : {}),
          ...(res.draft_id ? { draftId: res.draft_id } : {}),
          ...(res.needs_rating ? { needsRating: "1" } : {}),
        },
      });
    } catch (e: any) {
      setError(e.message || "Research failed");
    } finally {
      setResearching(false);
      setStatus("");
    }
  };

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }} edges={["top"]}>
        <ScrollView contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
          <View style={styles.topRow}>
            <BrandMark size="sm" />
            <View style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
              <Pressable
                onPress={() => router.push("/(app)/crm")}
                style={{
                  backgroundColor: colors.mist,
                  width: 36,
                  height: 36,
                  borderRadius: 10,
                  alignItems: "center",
                  justifyContent: "center",
                }}
                accessibilityLabel="People"
              >
                <Text style={{ color: colors.forest, fontSize: 16 }}>⌘</Text>
              </Pressable>
              <Pressable
                onPress={() => router.push("/(app)/profile")}
                style={{
                  backgroundColor: colors.mist,
                  width: 36,
                  height: 36,
                  borderRadius: 10,
                  alignItems: "center",
                  justifyContent: "center",
                }}
                accessibilityLabel="You"
              >
                <Text style={{ color: colors.forest, fontSize: 16 }}>◌</Text>
              </Pressable>
              <Pressable
                onPress={toggle}
                style={{
                  backgroundColor: colors.mist,
                  paddingHorizontal: 12,
                  paddingVertical: 8,
                  borderRadius: 999,
                }}
              >
                <Text style={{ fontFamily: fonts.bodySemi, color: colors.forest, fontSize: 12 }}>
                  {mode === "dark" ? "Light" : "Dark"}
                </Text>
              </Pressable>
              <View style={{ backgroundColor: colors.mist, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999 }}>
                <Text style={{ fontFamily: fonts.bodySemi, color: colors.forest, fontSize: 13 }}>
                  {user?.tokens ?? 0} tokens
                </Text>
              </View>
            </View>
          </View>          <Text style={[styles.hero, { color: colors.ink }]}>Who are you meeting?</Text>
          <Text style={[styles.sub, { color: colors.muted }]}>
            Full name plus company, school, or LinkedIn. When names collide, pick the row.
          </Text>

          <Field label="Full name" value={name} onChangeText={setName} placeholder="Exact name" />
          <Field label="Company" value={company} onChangeText={setCompany} placeholder="Required if no school/LinkedIn" />
          <Field label="University" value={university} onChangeText={setUniversity} placeholder="Optional" />
          <Field
            label="LinkedIn URL"
            value={linkedin}
            onChangeText={setLinkedin}
            autoCapitalize="none"
            placeholder="https://linkedin.com/in/…"
          />

          <Text style={[styles.label, { color: colors.moss }]}>Tier</Text>
          <View style={styles.tierRow}>
            {(["basic", "detailed"] as const).map((t) => (
              <Pressable
                key={t}
                onPress={() => setTier(t)}
                style={[
                  styles.tier,
                  { borderColor: colors.line, backgroundColor: colors.chalk },
                  tier === t && { borderColor: colors.forest, backgroundColor: colors.mist },
                ]}
              >
                <Text
                  style={{
                    fontFamily: fonts.bodyMed,
                    color: tier === t ? colors.forest : colors.muted,
                  }}
                >
                  {t === "basic" ? "Basic · 1" : "Detailed · 3"}
                </Text>
              </Pressable>
            ))}
          </View>

          <Button title="Find matches" onPress={findPeople} loading={loadingCandidates} style={{ marginTop: 8 }} />
          <Button
            title={researching ? "Working…" : `Research ${tier}`}
            onPress={() => runResearch()}
            loading={researching}
            variant="ember"
            style={{ marginTop: 10 }}
            disabled={!hasDisambiguator() && candidates.length === 0}
          />

          {status ? <Text style={{ marginTop: 12, fontFamily: fonts.body, color: colors.leaf }}>{status}</Text> : null}
          {error ? <Text style={{ marginTop: 12, fontFamily: fonts.bodyMed, color: colors.danger }}>{error}</Text> : null}
          {researching ? <ActivityIndicator color={colors.forest} style={{ marginTop: 12 }} /> : null}

          {candidates.map((c, i) => (
            <Pressable
              key={`${c.name}-${i}`}
              style={{
                marginTop: 12,
                padding: 14,
                borderRadius: 16,
                backgroundColor: colors.chalk,
                borderWidth: 1,
                borderColor: colors.line,
                flexDirection: "row",
                gap: 12,
                alignItems: "center",
              }}
              onPress={() => {
                setName(c.name);
                if (c.company) setCompany(c.company);
                if (c.linkedin_url) setLinkedin(c.linkedin_url);
                setMustPick(false);
                runResearch(c);
              }}
            >
              {c.photo_url ? (
                <Image
                  source={{ uri: c.photo_url }}
                  style={{ width: 48, height: 48, borderRadius: 24, backgroundColor: colors.mist }}
                />
              ) : (
                <View
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 24,
                    backgroundColor: colors.mist,
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Text style={{ fontFamily: fonts.bodySemi, color: colors.forest }}>
                    {(c.name || "?")[0].toUpperCase()}
                  </Text>
                </View>
              )}
              <View style={{ flex: 1 }}>
                <Text style={{ fontFamily: fonts.bodySemi, fontSize: 16, color: colors.ink }}>{c.name}</Text>
                <Text style={{ fontFamily: fonts.body, fontSize: 13, color: colors.muted, marginTop: 4 }}>
                  {[c.name, c.company, c.role].filter(Boolean).join(" · ")}
                </Text>
              </View>
            </Pressable>
          ))}
        </ScrollView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 40 },
  topRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  hero: {
    fontFamily: fonts.display,
    fontSize: 34,
    marginTop: space.lg,
    letterSpacing: -0.6,
  },
  sub: {
    fontFamily: fonts.body,
    marginTop: 8,
    marginBottom: space.lg,
    lineHeight: 21,
  },
  label: {
    fontFamily: fonts.bodyMed,
    fontSize: 13,
    marginBottom: 6,
    textTransform: "uppercase",
    letterSpacing: 0.8,
  },
  tierRow: { flexDirection: "row", gap: 10, marginBottom: space.md },
  tier: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 14,
    paddingVertical: 12,
    alignItems: "center",
  },
});
