import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/api_client.dart';
import '../auth/auth_provider.dart';

class StaffHomeScreen extends StatefulWidget {
  const StaffHomeScreen({super.key});

  @override
  State<StaffHomeScreen> createState() => _StaffHomeScreenState();
}

class _StaffHomeScreenState extends State<StaffHomeScreen> {
  List<dynamic> _payments = [];
  bool _loading = true;
  String? _error;
  final _loanController = TextEditingController();
  final _amountController = TextEditingController();
  final _remarksController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _loadCollections();
  }

  Future<void> _loadCollections() async {
    final auth = Provider.of<AuthProvider>(context, listen: false);
    final client = ApiClient(token: auth.token);
    final response = await client.get('/staff/today-collections');
    setState(() {
      _loading = false;
      if (response['error'] != null) {
        _error = response['error'];
      } else {
        _payments = response['data'];
      }
    });
  }

  Future<void> _recordPayment() async {
    final auth = Provider.of<AuthProvider>(context, listen: false);
    final client = ApiClient(token: auth.token);
    final payload = {
      'loan_id': int.tryParse(_loanController.text),
      'amount_collected': double.tryParse(_amountController.text) ?? 0,
      'payment_method': 'Cash',
      'remarks': _remarksController.text,
    };
    final response = await client.post('/staff/payments', payload);
    if (response['error'] != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(response['error'])),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Payment recorded')),
      );
      _loanController.clear();
      _amountController.clear();
      _remarksController.clear();
      _loadCollections();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Staff Dashboard')),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Record Payment', style: TextStyle(fontWeight: FontWeight.bold)),
            TextField(
              controller: _loanController,
              decoration: const InputDecoration(labelText: 'Loan ID'),
              keyboardType: TextInputType.number,
            ),
            TextField(
              controller: _amountController,
              decoration: const InputDecoration(labelText: 'Amount'),
              keyboardType: TextInputType.number,
            ),
            TextField(
              controller: _remarksController,
              decoration: const InputDecoration(labelText: 'Remarks (optional)'),
            ),
            const SizedBox(height: 12),
            ElevatedButton(
              onPressed: _recordPayment,
              child: const Text('Submit Payment'),
            ),
            const SizedBox(height: 20),
            const Text("Today's Collections", style: TextStyle(fontWeight: FontWeight.bold)),
            Expanded(
              child: _loading
                  ? const Center(child: CircularProgressIndicator())
                  : _error != null
                      ? Center(child: Text(_error!))
                      : ListView.builder(
                          itemCount: _payments.length,
                          itemBuilder: (context, index) {
                            final p = _payments[index];
                            return ListTile(
                              title: Text('LKR ${p['amount_collected']}'),
                              subtitle: Text('Loan ${p['loan_id']} - ${p['payment_method']}'),
                              trailing: Text(p['collection_date']),
                            );
                          },
                        ),
            )
          ],
        ),
      ),
    );
  }
}
