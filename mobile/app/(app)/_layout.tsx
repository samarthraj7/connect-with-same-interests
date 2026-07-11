import { Tabs } from "expo-router";
import { Text } from "react-native";
import { colors, fonts } from "../../lib/theme";

function TabLabel({ label, focused }: { label: string; focused: boolean }) {
  return (
    <Text
      style={{
        fontFamily: focused ? fonts.bodySemi : fonts.body,
        fontSize: 12,
        color: focused ? colors.ember : colors.muted,
      }}
    >
      {label}
    </Text>
  );
}

export default function AppLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: colors.chalk,
          borderTopColor: colors.line,
          height: 64,
          paddingBottom: 8,
          paddingTop: 8,
        },
        tabBarActiveTintColor: colors.ember,
        tabBarInactiveTintColor: colors.muted,
      }}
    >
      <Tabs.Screen
        name="home"
        options={{
          title: "Search",
          tabBarLabel: ({ focused }) => <TabLabel label="Search" focused={focused} />,
          tabBarIcon: () => null,
        }}
      />
      <Tabs.Screen
        name="crm"
        options={{
          title: "People",
          tabBarLabel: ({ focused }) => <TabLabel label="People" focused={focused} />,
          tabBarIcon: () => null,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: "You",
          tabBarLabel: ({ focused }) => <TabLabel label="You" focused={focused} />,
          tabBarIcon: () => null,
        }}
      />
      <Tabs.Screen name="person/[name]" options={{ href: null }} />
    </Tabs>
  );
}
