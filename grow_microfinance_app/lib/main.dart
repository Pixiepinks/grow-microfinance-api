import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'features/auth/auth_provider.dart';
import 'features/auth/login_screen.dart';
import 'features/admin/admin_home_screen.dart';
import 'features/staff/staff_home_screen.dart';
import 'features/customer/customer_dashboard.dart';

void main() {
  runApp(const GrowMicrofinanceApp());
}

class GrowMicrofinanceApp extends StatelessWidget {
  const GrowMicrofinanceApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => AuthProvider(),
      child: MaterialApp(
        title: 'Grow Microfinance',
        theme: ThemeData(primarySwatch: Colors.green),
        home: const SplashDecider(),
      ),
    );
  }
}

class SplashDecider extends StatefulWidget {
  const SplashDecider({super.key});

  @override
  State<SplashDecider> createState() => _SplashDeciderState();
}

class _SplashDeciderState extends State<SplashDecider> {
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _checkAuth();
  }

  Future<void> _checkAuth() async {
    final auth = Provider.of<AuthProvider>(context, listen: false);
    final hasCreds = await auth.tryAutoLogin();
    if (!mounted) return;
    if (hasCreds) {
      _goToRole(auth.role!);
    } else {
      setState(() => _loading = false);
    }
  }

  void _goToRole(String role) {
    Widget screen;
    switch (role) {
      case 'admin':
        screen = const AdminHomeScreen();
        break;
      case 'staff':
        screen = const StaffHomeScreen();
        break;
      default:
        screen = const CustomerDashboard();
    }
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(builder: (_) => screen),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    return const LoginScreen();
  }
}
