import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { api, API_BASE } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fonts, space } from "../lib/theme";
import { useTheme } from "../lib/theme-context";
import { Button, ChipInput, Field } from "./ui";

type Props = { visible: boolean; onClose: () => void };

type Candidate = {
  name: string;
  role?: string;
  company?: string;
  location?: string;
  linkedin_url?: string;
  photo_url?: string;
  context?: string;
};

function CandidateAvatar({
  name,
  photoUrl,
  mist,
  forest,
}: {
  name: string;
  photoUrl?: string;
  mist: string;
  forest: string;
}) {
  const [broken, setBroken] = useState(false);
  const showImg = !!photoUrl && !broken;
  if (showImg) {
    return (
      <Image
        source={{ uri: photoUrl }}
        onError={() => setBroken(true)}
        style={{ width: 48, height: 48, borderRadius: 24, backgroundColor: mist }}
      />
    );
  }
  return (
    <View
      style={{
        width: 48,
        height: 48,
        borderRadius: 24,
        backgroundColor: mist,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Text style={{ fontFamily: fonts.bodySemi, color: forest, fontSize: 16 }}>
        {(name || "?")[0].toUpperCase()}
      </Text>
    </View>
  );
}

const PHASES = [
  "Creating your account…",
  "Searching public web & news…",
  "Portfolios, blogs & talks…",
  "Company / school / Apollo…",
  "Building your public profile…",
  "Saving full dossier to the database…",
];

/**
 * Name-first signup sheet:
 * 1) name only → 2) optional filters + candidate pick → 3) email/password
 * → 4) researching (visible progress) → 5) confirm public + private tips
 */
export function SignupSheet({ visible, onClose }: Props) {
  const { colors } = useTheme();
  const { setSession, refresh } = useAuth();
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("");
  const [showFilters, setShowFilters] = useState(false);

  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [university, setUniversity] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [picked, setPicked] = useState<Candidate | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [privateDraft, setPrivateDraft] = useState("");
  const [hobbies, setHobbies] = useState<string[]>([]);
  const [interests, setInterests] = useState<string[]>([]);
  const [sports, setSports] = useState<string[]>([]);
  const [headline, setHeadline] = useState("");

  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!visible) {
      setStep(0);
      setError("");
      setProgress(0);
      setPhase("");
      setCandidates([]);
      setPicked(null);
      setShowFilters(false);
      if (timer.current) clearInterval(timer.current);
    } else {
      console.log("[SignupSheet] open — API_BASE=", API_BASE);
    }
  }, [visible]);

  const findCandidates = async () => {
    setError("");
    if (!name.trim()) {
      setError("Enter your full name.");
      return;
    }
    setLoading(true);
    const payload = {
      name: name.trim(),
      company: company.trim() || null,
      university: university.trim() || null,
      linkedin_url: linkedin.trim() || null,
    };
    const url = `${API_BASE}/public/candidates`;
    console.log("[Find me] POST", url, payload);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const text = await res.text();
      console.log("[Find me] status", res.status, "body", text.slice(0, 2000));
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        throw new Error(`Bad response from ${url}: ${text.slice(0, 200)}`);
      }
      if (!res.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      const list = data.candidates || [];
      setCandidates(list);
      if (data.warning === "no_public_matches" || (!list.length && !data.error)) {
        setError("No public matches — tap the name card (or add company/LinkedIn) to continue.");
      } else if (!list.length) {
        setError(
          data.error
            ? `Search issue: ${data.error}`
            : "No matches yet — adjust filters or continue with your name + optional company/LinkedIn.",
        );
      } else if (list.length === 1 && (list[0].context || "").toLowerCase().includes("no public")) {
        setError("No public matches found — select this card or add company/LinkedIn filters.");
      }
      setStep(1);
    } catch (e: any) {
      console.error("[Find me] FAILED", e);
      setError(`${e.message || "Search failed"} (API: ${API_BASE})`);
    } finally {
      setLoading(false);
    }
  };

  const startProgressLoop = () => {
    setProgress(0.08);
    setPhase(PHASES[0]);
    let i = 0;
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(() => {
      i = Math.min(i + 1, PHASES.length - 1);
      setPhase(PHASES[i]);
      setProgress((p) => Math.min(0.9, p + 0.14));
    }, 2800);
  };

  const stopProgress = (ok: boolean) => {
    if (timer.current) clearInterval(timer.current);
    setProgress(ok ? 1 : 0);
    setPhase(ok ? "Done — reviewing your public profile" : "");
  };

  const createAndResearch = async () => {
    setError("");
    if (!email.trim() || password.length < 6) {
      setError("Email and password (6+ chars) required to own the account.");
      return;
    }
    const who = picked?.name || name.trim();
    const co = picked?.company || company.trim() || null;
    const li = picked?.linkedin_url || linkedin.trim() || null;
    const uni = university.trim() || null;
    if (!co && !uni && !li) {
      setError("Pick a candidate or add company / university / LinkedIn before researching.");
      return;
    }

    setStep(3);
    setLoading(true);
    startProgressLoop();
    // Let the progress UI paint before the long request
    await new Promise((r) => setTimeout(r, 80));

    try {
      const signup = await api.signup({
        name: who,
        email: email.trim(),
        password,
        company: co || "",
        university: uni || "",
        linkedin_url: li || "",
        place: picked?.location || "",
        location: picked?.location || "",
        hobbies,
        interests,
        sports,
        research_me: false,
        talking_goals: ["Find genuine common ground before meetings"],
      });
      await setSession(signup.token, signup.user);

      const researched = await api.researchMe({
        company: co,
        university: uni,
        linkedin_url: li,
        force_refresh: true,
      });
      await refresh();
      const p = researched.user?.profile || {};
      setHeadline(p.headline || picked?.role || "");
      setHobbies(p.hobbies || hobbies);
      setInterests(p.interests || interests);
      setSports(p.sports || sports);
      // Mark claim on public dossier via profile fields already set by researchMe
      stopProgress(true);
      setStep(4);
    } catch (e: any) {
      stopProgress(false);
      setError(e.message || "Signup / research failed");
      setStep(2);
    } finally {
      setLoading(false);
    }
  };

  const finish = async () => {
    setLoading(true);
    setError("");
    try {
      await api.updateProfile({ headline, hobbies, interests, sports });
      if (privateDraft.trim()) {
        await api.addPrivateJournal({ body: privateDraft.trim(), entry_type: "blog" });
      }
      await refresh();
      onClose();
    } catch (e: any) {
      setError(e.message || "Could not save");
    } finally {
      setLoading(false);
    }
  };

  const titles = [
    "What’s your name?",
    "Is this you?",
    "Create login",
    "Researching you",
    "Public + private",
  ];

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ width: "100%" }}>
          <Pressable
            style={[styles.sheet, { backgroundColor: colors.chalk, borderColor: colors.line }]}
            onPress={(e) => e.stopPropagation()}
          >
            <View style={[styles.handle, { backgroundColor: colors.line }]} />
            <Text style={[styles.title, { color: colors.ink }]}>{titles[step]}</Text>
            <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 10, fontSize: 13 }}>
              Step {step + 1} of 5
            </Text>

            {/* Progress always mounted when researching so it stays visible */}
            {(step === 3 || progress > 0) && step !== 4 ? (
              <View style={[styles.progressBox, { backgroundColor: colors.mist, borderColor: colors.line }]}>
                <Text style={{ fontFamily: fonts.bodySemi, color: colors.forest, marginBottom: 8 }}>
                  {Math.round(progress * 100)}%
                </Text>
                <View style={[styles.barTrack, { backgroundColor: colors.chalk }]}>
                  <View
                    style={[
                      styles.barFill,
                      { width: `${Math.max(6, Math.round(progress * 100))}%`, backgroundColor: colors.ember },
                    ]}
                  />
                </View>
                <Text style={{ marginTop: 10, fontFamily: fonts.body, color: colors.moss }}>{phase}</Text>
                {loading ? <ActivityIndicator color={colors.forest} style={{ marginTop: 12 }} /> : null}
              </View>
            ) : null}

            <ScrollView style={{ maxHeight: 440 }} keyboardShouldPersistTaps="handled">
              {step === 0 && (
                <View>
                  <Field label="Full name" value={name} onChangeText={setName} placeholder="As it appears professionally" />
                  <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 8, lineHeight: 20 }}>
                    We’ll suggest people who match this name. Everything else is optional.
                  </Text>
                  <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 8, fontSize: 11 }}>
                    API: {API_BASE}
                  </Text>
                </View>
              )}

              {step === 1 && (
                <View>
                  <Pressable onPress={() => setShowFilters((s) => !s)}>
                    <Text style={{ fontFamily: fonts.bodyMed, color: colors.leaf, marginBottom: 8 }}>
                      {showFilters ? "Hide filters" : "Optional filters (company / university / LinkedIn)"}
                    </Text>
                  </Pressable>
                  {showFilters ? (
                    <View>
                      <Field label="Company" value={company} onChangeText={setCompany} />
                      <Field label="University" value={university} onChangeText={setUniversity} />
                      <Field label="LinkedIn URL or id" autoCapitalize="none" value={linkedin} onChangeText={setLinkedin} />
                      <Button title="Re-filter matches" onPress={findCandidates} loading={loading} style={{ marginBottom: 10 }} />
                    </View>
                  ) : null}

                  {candidates.map((c, i) => (
                    <Pressable
                      key={`${c.name}-${i}`}
                      onPress={() => {
                        setPicked(c);
                        if (c.company) setCompany(c.company);
                        if (c.linkedin_url) setLinkedin(c.linkedin_url);
                      }}
                      style={{
                        padding: 12,
                        borderRadius: 14,
                        borderWidth: 1,
                        borderColor: picked === c ? colors.forest : colors.line,
                        backgroundColor: picked === c ? colors.mist : colors.chalk,
                        marginBottom: 8,
                        flexDirection: "row",
                        gap: 12,
                        alignItems: "center",
                      }}
                    >
                      <CandidateAvatar
                        name={c.name}
                        photoUrl={c.photo_url}
                        mist={colors.mist}
                        forest={colors.forest}
                      />
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontFamily: fonts.bodySemi, color: colors.ink }}>{c.name}</Text>
                        <Text style={{ fontFamily: fonts.body, color: colors.muted, marginTop: 4, fontSize: 13 }}>
                          {[c.company, c.role].filter(Boolean).join(" · ") || c.context || ""}
                        </Text>
                      </View>
                    </Pressable>
                  ))}
                  {!candidates.length ? (
                    <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 8 }}>
                      Continue with filters if search was empty.
                    </Text>
                  ) : null}
                </View>
              )}

              {step === 2 && (
                <View>
                  <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 10 }}>
                    Selected: {(picked?.name || name) + (picked?.company ? ` · ${picked.company}` : "")}
                  </Text>
                  <Field label="Email" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
                  <Field label="Password" secureTextEntry value={password} onChangeText={setPassword} />
                </View>
              )}

              {step === 4 && (
                <View>
                  <Text style={{ fontFamily: fonts.bodySemi, color: colors.forest, marginBottom: 6 }}>Public profile</Text>
                  <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 10, lineHeight: 20 }}>
                    This dossier is what others see when they search you — career, portfolios, writing, not private journals.
                  </Text>
                  <Field label="Headline" value={headline} onChangeText={setHeadline} />
                  <ChipInput label="Hobbies (public if you want)" value={hobbies} onChange={setHobbies} />
                  <ChipInput label="Interests" value={interests} onChange={setInterests} />
                  <ChipInput label="Sports" value={sports} onChange={setSports} />

                  <Text style={{ fontFamily: fonts.bodySemi, color: colors.ember, marginTop: 16, marginBottom: 6 }}>
                    Private journal
                  </Text>
                  <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 8, lineHeight: 20 }}>
                    Only used for overlap when someone researches you — never shown on your public page.
                  </Text>
                  <Field
                    label="First private note / blog"
                    value={privateDraft}
                    onChangeText={setPrivateDraft}
                    placeholder="Things you care about this week…"
                    multiline
                  />
                </View>
              )}

              {error ? (
                <Text style={{ color: colors.danger, fontFamily: fonts.bodyMed, marginVertical: 8 }}>{error}</Text>
              ) : null}
            </ScrollView>

            <View style={{ marginTop: 12, gap: 8 }}>
              {step === 0 && <Button title="Find me" onPress={findCandidates} loading={loading} variant="ember" />}
              {step === 1 && (
                <Button
                  title="Continue with selection"
                  onPress={() => {
                    if (!picked && !company.trim() && !university.trim() && !linkedin.trim()) {
                      setError("Pick a candidate or add a filter.");
                      return;
                    }
                    setStep(2);
                  }}
                  variant="ember"
                />
              )}
              {step === 2 && (
                <Button title="Create account & research" onPress={createAndResearch} loading={loading} variant="ember" />
              )}
              {step === 4 && <Button title="Finish" onPress={finish} loading={loading} variant="ember" />}
              {step > 0 && step < 3 ? (
                <Button title="Back" variant="ghost" onPress={() => setStep((s) => s - 1)} disabled={loading} />
              ) : null}
              <Button title="Close" variant="ghost" onPress={onClose} disabled={loading && step === 3} />
            </View>
          </Pressable>
        </KeyboardAvoidingView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.5)" },
  sheet: {
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    borderWidth: 1,
    padding: space.lg,
    paddingBottom: space.xl,
    maxHeight: "94%",
  },
  handle: { alignSelf: "center", width: 40, height: 4, borderRadius: 2, marginBottom: 12 },
  title: { fontFamily: fonts.display, fontSize: 26, marginBottom: 4 },
  progressBox: {
    borderWidth: 1,
    borderRadius: 16,
    padding: 14,
    marginBottom: 12,
  },
  barTrack: { height: 12, borderRadius: 8, overflow: "hidden" },
  barFill: { height: 12, borderRadius: 8 },
});
