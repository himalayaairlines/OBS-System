/**
 * Branded start-up screen, shown while fonts and the saved session load.
 * Same navy gradient as the login hero, the OBS app mark (Himalaya Airlines
 * logo with the onboard-sales badge), and a short feature strip.
 * Works on web, iOS and Android; no extra packages needed.
 */
import React, { useEffect, useRef } from 'react';
import { ActivityIndicator, Animated, Image, StatusBar, View } from 'react-native';
import { Text } from './text';
import Svg, { Defs, LinearGradient, RadialGradient, Rect, Stop } from 'react-native-svg';

import { ws } from './fonts';

const LOGO = require('../assets/splash-logo.png');
const LOGO_RATIO = 1; // splash-logo.png is square (the app icon)

export default function SplashScreen() {
  const fade = useRef(new Animated.Value(0)).current;
  const rise = useRef(new Animated.Value(12)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fade, { toValue: 1, duration: 450, useNativeDriver: true }),
      Animated.timing(rise, { toValue: 0, duration: 450, useNativeDriver: true }),
    ]).start();
  }, [fade, rise]);

  return (
    <View style={{ flex: 1, backgroundColor: '#0A1A3F' }} accessibilityLabel="Himalaya Airlines OBS is starting">
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
      <Svg style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} width="100%" height="100%"
        preserveAspectRatio="none">
        <Defs>
          <LinearGradient id="splashSky" x1="0" y1="0" x2="1" y2="1">
            <Stop offset="0" stopColor="#0A1A3F" />
            <Stop offset="0.55" stopColor="#10306B" />
            <Stop offset="1" stopColor="#1D4E9E" />
          </LinearGradient>
          <RadialGradient id="splashGlow" cx="50%" cy="100%" r="70%">
            <Stop offset="0" stopColor="#2F6FED" stopOpacity="0.35" />
            <Stop offset="1" stopColor="#2F6FED" stopOpacity="0" />
          </RadialGradient>
        </Defs>
        <Rect x="0" y="0" width="100%" height="100%" fill="url(#splashSky)" />
        <Rect x="0" y="0" width="100%" height="100%" fill="url(#splashGlow)" />
      </Svg>

      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <Animated.View style={{ alignItems: 'center', opacity: fade, transform: [{ translateY: rise }] }}>
          <Image
            source={LOGO}
            style={{ width: 132, height: 132 / LOGO_RATIO }}
            resizeMode="contain"
            accessibilityLabel="Himalaya Airlines logo"
          />
          <Text style={{ ...ws(700), color: '#FFFFFF', fontSize: 24, letterSpacing: 1.5, marginTop: 28, textAlign: 'center' }}>
            HIMALAYA AIRLINES
          </Text>
          <Text style={{ ...ws(400), color: '#CDDEF3', fontSize: 15, marginTop: 6, textAlign: 'center' }}>
            Onboard Sales Revenue System
          </Text>
          <View style={{ width: 40, height: 3, borderRadius: 2, backgroundColor: '#F26637', marginTop: 16 }} />
          <ActivityIndicator color="#FFFFFF" style={{ marginTop: 32 }} />
        </Animated.View>
      </View>

      <Text style={{
        ...ws(500), color: '#A0BADE', fontSize: 11, letterSpacing: 1.2, textAlign: 'center', marginBottom: 36,
      }}>
        SALES  ·  STOCK  ·  CREW  ·  WAREHOUSE
      </Text>
    </View>
  );
}
