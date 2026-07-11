import { useFocusEffect, useRouter } from "expo-router";
import React, { useCallback, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { SectionTitle } from "../../components/ui";
import { api } from "../../lib/api";
import { colors, fonts, space } from "../../lib/theme";

export default function Crm() {
  const router = useRouter();
  const [people, setPeople] = useState<any[]>([]);
  const [error, setError] = useState("");

  useFocusEffect(
    useCallback(() => {
      (async () => {
        try {
          const res = await api.people();
          setPeople(res.people || []);
        } catch (e: any) {
          setError(e.message || "Failed to load");
        }
      })();
    }, [])
  );

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }} edges={["top"]}>
        <ScrollView contentContainerStyle={styles.pad}>
          <SectionTitle>Your people</SectionTitle>
          <Text style={styles.sub}>Saved research, contacts, and overlap — your personal CRM.</Text>
          {error ? <Text style={styles.err}>{error}</Text> : null}
          {!people.length ? (
            <Text style={styles.empty}>No one yet. Search someone to start building your list.</Text>
          ) : (
            people.map((p) => (
              <Pressable
                key={`${p.name}-${p.company}`}
                style={styles.row}
                onPress={() =>
                  router.push({
                    pathname: "/(app)/person/[name]",
                    params: { name: p.name, company: p.company || "" },
                  })
                }
              >
                <View style={{ flex: 1 }}>
                  <Text style={styles.name}>{p.name}</Text>
                  <Text style={styles.meta}>
                    {[p.company, p.contact?.linkedin_url ? "LinkedIn" : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </Text>
                </View>
                {p.overlap_score != null ? (
                  <Text style={styles.score}>{p.overlap_score}</Text>
                ) : null}
              </Pressable>
            ))
          )}
        </ScrollView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 40 },
  sub: { fontFamily: fonts.body, color: colors.muted, marginBottom: space.lg, lineHeight: 21 },
  empty: { fontFamily: fonts.body, color: colors.muted, marginTop: 20 },
  err: { color: colors.danger, fontFamily: fonts.bodyMed },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
    gap: 12,
  },
  name: { fontFamily: fonts.bodySemi, fontSize: 16, color: colors.ink },
  meta: { fontFamily: fonts.body, fontSize: 13, color: colors.muted, marginTop: 2 },
  score: {
    fontFamily: fonts.display,
    fontSize: 20,
    color: colors.ember,
  },
});
