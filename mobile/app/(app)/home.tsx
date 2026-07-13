import { useRouter } from "expo-router";
import React, { useState } from "react";
import {
  ActivityIndicator,
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
import { colors, fonts, space } from "../../lib/theme";

type Candidate = {
  name: string;
  role?: string;
  company?: string;
  location?: string;
  linkedin_url?: string;
};

export default function Home() {
  const { user, refresh } = useAuth();
  const router = useRouter();
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [tier, setTier] = useState<"basic" | "detailed">("detailed");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [researching, setResearching] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const findPeople = async () => {
    setError("");
    setCandidates([]);
    if (!name.trim()) {
      setError("Enter a name to search.");
      return;
    }
    setLoadingCandidates(true);
    try {
      const res = await api.candidates(name.trim());
      setCandidates(res.candidates || []);
      if (!(res.candidates || []).length) setStatus("No disambiguation hits — you can still research directly.");
      else setStatus("Pick someone, or research with the fields below.");
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
      setError("Name required.");
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
        company: override?.company || company.trim() || null,
        linkedin_url: override?.linkedin_url || null,
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
            <View style={styles.tokenPill}>
              <Text style={styles.tokenText}>{user?.tokens ?? 0} tokens</Text>
            </View>
          </View>
          <Text style={styles.hero}>Who are you meeting?</Text>
          <Text style={styles.sub}>
            Basic (1) = who they are. Detailed (3) = briefing plus things to talk about and openers.
          </Text>

          <Field label="Name" value={name} onChangeText={setName} placeholder="Full name" />
          <Field label="Company (optional)" value={company} onChangeText={setCompany} />

          <Text style={styles.label}>Tier</Text>
          <View style={styles.tierRow}>
            {(["basic", "detailed"] as const).map((t) => (
              <Pressable
                key={t}
                onPress={() => setTier(t)}
                style={[styles.tier, tier === t && styles.tierOn]}
              >
                <Text style={[styles.tierText, tier === t && styles.tierTextOn]}>
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
          />

          {status ? <Text style={styles.status}>{status}</Text> : null}
          {error ? <Text style={styles.err}>{error}</Text> : null}
          {researching ? <ActivityIndicator color={colors.forest} style={{ marginTop: 12 }} /> : null}

          {candidates.map((c, i) => (
            <Pressable key={`${c.name}-${i}`} style={styles.card} onPress={() => runResearch(c)}>
              <Text style={styles.cardName}>{c.name}</Text>
              <Text style={styles.cardMeta}>
                {[c.role, c.company, c.location].filter(Boolean).join(" · ")}
              </Text>
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
  tokenPill: {
    backgroundColor: colors.mist,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
  },
  tokenText: { fontFamily: fonts.bodySemi, color: colors.forest, fontSize: 13 },
  hero: {
    fontFamily: fonts.display,
    fontSize: 34,
    color: colors.ink,
    marginTop: space.lg,
    letterSpacing: -0.6,
  },
  sub: {
    fontFamily: fonts.body,
    color: colors.muted,
    marginTop: 8,
    marginBottom: space.lg,
    lineHeight: 21,
  },
  label: {
    fontFamily: fonts.bodyMed,
    fontSize: 13,
    color: colors.moss,
    marginBottom: 6,
    textTransform: "uppercase",
    letterSpacing: 0.8,
  },
  tierRow: { flexDirection: "row", gap: 10, marginBottom: space.md },
  tier: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: 14,
    paddingVertical: 12,
    alignItems: "center",
    backgroundColor: "rgba(251,252,250,0.7)",
  },
  tierOn: { borderColor: colors.forest, backgroundColor: colors.mist },
  tierText: { fontFamily: fonts.bodyMed, color: colors.muted },
  tierTextOn: { color: colors.forest },
  status: { marginTop: 12, fontFamily: fonts.body, color: colors.leaf },
  err: { marginTop: 12, fontFamily: fonts.bodyMed, color: colors.danger },
  card: {
    marginTop: 12,
    padding: 14,
    borderRadius: 16,
    backgroundColor: "rgba(251,252,250,0.9)",
    borderWidth: 1,
    borderColor: colors.line,
  },
  cardName: { fontFamily: fonts.bodySemi, fontSize: 16, color: colors.ink },
  cardMeta: { fontFamily: fonts.body, fontSize: 13, color: colors.muted, marginTop: 4 },
});
