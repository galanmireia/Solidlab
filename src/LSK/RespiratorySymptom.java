package LSK;

public class RespiratorySymptom extends Symptom implements 	Curable{

	public RespiratorySymptom(int covidImpact, int severityIndex, String name) {
		super(severityIndex, severityIndex, name);

	}

	@Override
	public void cure() {
		// TODO Auto-generated method stub
		System.out.println("Sendatu egin da RespiratorySymptom");
		
	}


}
