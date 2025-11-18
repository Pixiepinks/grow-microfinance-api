import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../auth/auth_provider.dart';

class AdminHomeScreen extends StatefulWidget {
  const AdminHomeScreen({super.key});

  @override
  State<AdminHomeScreen> createState() => _AdminHomeScreenState();
}

class _AdminHomeScreenState extends State<AdminHomeScreen> {
  Map<String, dynamic>? _dashboard;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final auth = Provider.of<AuthProvider>(context, listen: false);
    final client = ApiClient(token: auth.token);
    final response = await client.get('/admin/dashboard');
    setState(() {
      _loading = false;
      if (response['error'] != null) {
        _error = response['error'];
      } else {
        _dashboard = response['data'];
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Admin Dashboard')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Total customers: ${_dashboard?['total_customers'] ?? 0}'),
                      Text('Active loans: ${_dashboard?['total_active_loans'] ?? 0}'),
                      Text('Outstanding: LKR ${_dashboard?['total_outstanding'] ?? 0}'),
                      Text("Today's collection: LKR ${_dashboard?['todays_collection'] ?? 0}"),
                      const SizedBox(height: 20),
                      const Text('Navigation'),
                      Wrap(
                        spacing: 12,
                        children: const [
                          Chip(label: Text('Users')),
                          Chip(label: Text('Customers')),
                          Chip(label: Text('Loans')),
                        ],
                      )
                    ],
                  ),
                ),
    );
  }
}
