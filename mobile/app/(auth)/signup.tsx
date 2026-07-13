import { useRouter } from "expo-router";
import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../components/ScreenBackdrop";
import { BrandMark, Button, ChipInput, Field } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors, fonts, space } from "../../lib/theme";

/**
 * Create account → research YOU with form + socials → save everything to JSON.
 * Form fields and socials are stored; research enriches the living profile.
 */
export default function Signup() {
  const router = useRouter();
  const { setSession } = useAuth();
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [company, setCompany] = useState("");
  const [university, setUniversity] = useState("");
  const [location, setLocation] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [instagram, setInstagram] = useState("");
  const [twitter, setTwitter] = useState("");
  const [github, setGithub] = useState("");
  const [phone, setPhone] = useState("");
  const [hobbies, setHobbies] = useState<string[]>([]);
  const [interests, setInterests] = useState<string[]>([]);
  const [sports, setSports] = useState<string[]>([]);

  const next = () => {
    setError("");
    if (step === 0) {
      if (!name.trim() || !email.trim() || password.length < 6) {
        setError("Name, email, and a password (6+ chars) are required.");
        return;
      }
    }
    if (step === 1) {
      if (!company.trim() && !university.trim() && !linkedin.trim() && !location.trim()) {
        setError("Add company, school, location, or LinkedIn so we research the right you.");
        return;
      }
    }
    setStep((s) => Math.min(s + 1, 2));
  };

  const submit = async () => {
    setError("");
    setLoading(true);
    setStatus("Saving your account…");
    try {
      setStatus("Researching your public profile + socials — this can take a minute…");
      const res = await api.signup({
        name: name.trim(),
        email: email.trim(),
        password,
        company: company.trim(),
        university: university.trim(),
        place: location.trim(),
        location: location.trim(),
        linkedin_url: linkedin.trim(),
        instagram_handle: instagram.trim(),
        twitter_handle: twitter.trim(),
        github_username: github.trim(),
        phone: phone.trim(),
        hobbies,
        interests,
        sports,
        research_me: true,
        talking_goals: [
          "Find genuine common ground before meetings",
          "Open warm, specific conversations",
        ],
      });
      await setSession(res.token, res.user);
      router.replace("/(app)/home");
    } catch (e: any) {
      setError(e.message || "Signup failed");
      setStatus("");
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
            <BrandMark size="sm" />
            <Text style={styles.step}>Step {step + 1} of 3</Text>
            <Text style={styles.title}>
              {step === 0 ? "Create account" : step === 1 ? "Find you online" : "Socials & extras"}
            </Text>
            <Text style={styles.sub}>
              {step === 0
                ? "We research your public footprint during signup and save everything to your account."
                : step === 1
                  ? "Company, school, city, or LinkedIn — used to research the right you."
                  : "Instagram and other socials help when your professional web footprint is thin."}
            </Text>

            {step === 0 && (
              <View>
                <Field label="Full name" value={name} onChangeText={setName} />
                <Field label="Email" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
                <Field label="Password" secureTextEntry value={password} onChangeText={setPassword} />
                <Field label="Phone (optional)" value={phone} onChangeText={setPhone} keyboardType="phone-pad" />
              </View>
            )}
            {step === 1 && (
              <View>
                <Field label="Company" value={company} onChangeText={setCompany} placeholder="Where you work / founded" />
                <Field label="University" value={university} onChangeText={setUniversity} placeholder="School" />
                <Field label="Location" value={location} onChangeText={setLocation} placeholder="City / region" />
                <Field label="LinkedIn URL" autoCapitalize="none" value={linkedin} onChangeText={setLinkedin} placeholder="https://linkedin.com/in/..." />
              </View>
            )}
            {step === 2 && (
              <View>
                <Field label="Instagram" autoCapitalize="none" value={instagram} onChangeText={setInstagram} placeholder="@handle" />
                <Field label="Twitter / X" autoCapitalize="none" value={twitter} onChangeText={setTwitter} placeholder="@handle" />
                <Field label="GitHub" autoCapitalize="none" value={github} onChangeText={setGithub} placeholder="username" />
                <ChipInput label="Hobbies (optional)" value={hobbies} onChange={setHobbies} />
                <ChipInput label="Interests (optional)" value={interests} onChange={setInterests} />
                <ChipInput label="Sports (optional)" value={sports} onChange={setSports} />
              </View>
            )}

            {loading ? (
              <View style={styles.loadingBox}>
                <ActivityIndicator color={colors.forest} />
                <Text style={styles.status}>{status || "Working…"}</Text>
              </View>
            ) : null}
            {error ? <Text style={styles.err}>{error}</Text> : null}

            {step < 2 ? (
              <Button title="Continue" onPress={next} disabled={loading} />
            ) : (
              <Button
                title={loading ? "Researching you…" : "Create account & research me"}
                onPress={submit}
                loading={loading}
                variant="ember"
              />
            )}
            <Button
              title={step === 0 ? "Back" : "Previous"}
              variant="ghost"
              disabled={loading}
              onPress={() => (step === 0 ? router.back() : setStep((s) => s - 1))}
              style={{ marginTop: 10 }}
            />
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 48 },
  step: {
    marginTop: space.lg,
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    letterSpacing: 1,
    textTransform: "uppercase",
    color: colors.leaf,
  },
  title: {
    fontFamily: fonts.display,
    fontSize: 32,
    color: colors.ink,
    marginTop: 6,
    letterSpacing: -0.5,
  },
  sub: {
    fontFamily: fonts.body,
    fontSize: 15,
    lineHeight: 22,
    color: colors.muted,
    marginVertical: space.md,
  },
  err: { color: colors.danger, fontFamily: fonts.bodyMed, marginBottom: 12 },
  loadingBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginBottom: 12,
    padding: 12,
    borderRadius: 12,
    backgroundColor: colors.mist,
  },
  status: { flex: 1, fontFamily: fonts.body, color: colors.forest, fontSize: 14 },
});
