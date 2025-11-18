import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../admin/admin_home_screen.dart';
import '../staff/staff_home_screen.dart';
import '../customer/customer_dashboard.dart';
import 'auth_provider.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  String? _error;

  @override
  Widget build(BuildContext context) {
    final auth = Provider.of<AuthProvider>(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Grow Microfinance Login')),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _emailController,
              decoration: const InputDecoration(labelText: 'Email'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _passwordController,
              decoration: const InputDecoration(labelText: 'Password'),
              obscureText: true,
            ),
            const SizedBox(height: 12),
            if (_error != null)
              Text(
                _error!,
                style: const TextStyle(color: Colors.red),
              ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: auth.loading
                    ? null
                    : () async {
                        final error = await auth.login(
                          _emailController.text,
                          _passwordController.text,
                        );
                        if (error != null) {
                          setState(() => _error = error);
                        } else {
                          if (!mounted) return;
                          _goToHome(context, auth.role!);
                        }
                      },
                child: auth.loading
                    ? const CircularProgressIndicator()
                    : const Text('Login'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _goToHome(BuildContext context, String role) {
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
}
